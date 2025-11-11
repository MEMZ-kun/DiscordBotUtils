import configparser
import logging
import asyncio # 利用例のために asyncio をインポート
from typing import AsyncGenerator

# SQLAlchemy の非同期機能に必要なコンポーネントをインポート
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
# Base のインポート方法を修正 (declarative_base は v2.0 で非推奨になるため)
from sqlalchemy.orm import declarative_base

# --- モデル定義の基底クラス ---
# アプリケーションで定義する全てのモデル（テーブル）は、この Base を継承します。
Base = declarative_base()


class DatabaseManager:
    """
    データベース接続とセッション管理を非同期で行うクラス。
    config.ini の設定に基づき、適切なDBエンジンを初期化します。
    """

    def __init__(self, config: configparser.ConfigParser, logger: logging.Logger):
        """
        機能名: コンストラクタ
        説明: ConfigとLoggerを受け取り、DB接続の準備を行います。
        引数:
            config (configparser.ConfigParser): ConfigManagerから取得した設定
            logger (logging.Logger): BotLoggerから取得したロガー
        戻り値: なし
        """
        self.logger = logger
        try:
            db_type = config.get('Database', 'Type', fallback='sqlite')
            db_dsn = config.get('Database', 'DSN', fallback='db/bot.db')
        except configparser.Error as e:
            self.logger.critical(f"DB設定 (config.ini) の読み取りに失敗しました: {e}", exc_info=True)
            raise

        # DBタイプに応じて非同期DSN(接続文字列)を構築
        full_dsn = self._build_dsn(db_type, db_dsn)
        
        # SQLiteの場合、DSNからファイルパスを抽出し、ディレクトリを作成
        if db_type == 'sqlite' and db_dsn:
            import os
            db_dir = os.path.dirname(db_dsn)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir)
                self.logger.info(f"データベースディレクトリ '{db_dir}' を作成しました。")

        try:
            # 非同期DBエンジンを作成
            self.engine = create_async_engine(full_dsn, echo=False)
            
            # 非同期セッションを作成するための 'メーカー' を設定
            self.session_maker = async_sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
            )
            self.logger.info(f"{db_type} データベースへの接続準備ができました。")

        except Exception as e:
            self.logger.critical(f"DBエンジンの作成に失敗しました: {e}", exc_info=True)
            raise

    def _build_dsn(self, db_type: str, db_dsn: str) -> str:
        """
        機能名: DSN(接続文字列)の構築
        説明: DBタイプに応じて、SQLAlchemyが要求する非同期DSNを構築します。
        引数:
            db_type (str): 'sqlite', 'postgresql' など
            db_dsn (str): 接続情報 (ファイルパスやURL)
        戻り値:
            str: 完全な非同期DSN
        """
        if db_type == 'sqlite':
            # SQLite の非同期 (aiosqlite)
            return f"sqlite+aiosqlite:///{db_dsn}"
        elif db_type == 'postgresql':
            # PostgreSQL の非同期 (asyncpg)
            return f"postgresql+asyncpg://{db_dsn}"
        elif db_type == 'mysql':
            # MySQL の非同期 (aiomysql)
            return f"mysql+aiomysql://{db_dsn}"
        else:
            self.logger.warn(f"未知のDBタイプ: {db_type}。SQLiteとして処理を試みます。")
            return f"sqlite+aiosqlite:///{db_dsn}"

    async def init_db_schema(self):
        """
        機能名: DBスキーマ(テーブル)の初期化
        説明:
            Baseを継承している全てのモデル（テーブル）をDBに作成します。
            通常、ボットの起動時に一度だけ呼び出します。
        引数: なし
        戻り値: なし
        """
        async with self.engine.begin() as conn:
            try:
                # Base.metadata.create_all は、存在しないテーブルのみを作成します
                await conn.run_sync(Base.metadata.create_all)
                self.logger.info("データベーススキーマの初期化・確認が完了しました。")
            except Exception as e:
                self.logger.error(f"DBスキーマの初期化中にエラーが発生しました: {e}", exc_info=True)
                
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        機能名: 非同期セッションの取得 (Context Manager)
        説明:
            CRUD操作を行うための非同期セッションを提供します。
            'async with db_manager.get_session() as session:' の形で使用します。
            セッションは自動的にコミット・ロールバック・クローズされます。
        引数: なし
        戻り値:
            AsyncGenerator[AsyncSession, None]: 非同期セッション
        """
        async with self.session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception as e:
                await session.rollback()
                self.logger.error(f"DBセッション中にエラーが発生しました: {e}", exc_info=True)
                raise


# --- CRUD操作のサンプル (リポジトリパターン) ---
# 実際のDB操作は、このようにモデル(テーブル)ごとに行うクラスを定義することを推奨します。

from sqlalchemy import Column, Integer, String, BigInteger, UniqueConstraint

class GuildSetting(Base):
    """ サーバーごとの設定をKVS（キー・バリュー）で保存するテーブル """
    __tablename__ = 'guild_settings'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, index=True)
    setting_key = Column(String(100), nullable=False)
    setting_value = Column(String(500))

    # guild_id と setting_key の組み合わせはユニーク（一意）であるべき
    __table_args__ = (
        UniqueConstraint('guild_id', 'setting_key', name='_guild_key_uc'),
    )

    def __repr__(self):
        return f"<GuildSetting(guild_id={self.guild_id}, key='{self.setting_key}', value='{self.setting_value}')>"


class GuildSettingRepository:
    """
    GuildSettingテーブルに対するCRUD操作を担当するクラス
    """

    def __init__(self, db_manager: DatabaseManager):
        """
        機能名: コンストラクタ
        説明: DatabaseManagerのインスタンスを受け取ります。
        引数:
            db_manager (DatabaseManager): DB接続を管理するマネージャー
        戻り値: なし
        """
        self.db_manager = db_manager

    async def set_setting(self, guild_id: int, key: str, value: str):
        """
        機能名: 設定の作成・更新 (Create / Update)
        """
        async with self.db_manager.get_session() as session:
            from sqlalchemy.future import select
            q = select(GuildSetting).where(
                GuildSetting.guild_id == guild_id, 
                GuildSetting.setting_key == key
            )
            result = await session.execute(q)
            existing_setting = result.scalars().first()
            
            if existing_setting:
                existing_setting.setting_value = value
                session.add(existing_setting)
            else:
                new_setting = GuildSetting(
                    guild_id=guild_id,
                    setting_key=key,
                    setting_value=value
                )
                session.add(new_setting)
            
            # self.db_manager.logger.debug(f"DB保存: {guild_id} - {key} = {value}")

    async def get_setting(self, guild_id: int, key: str) -> str | None:
        """
        機能名: 設定の読み取り (Read)
        """
        async with self.db_manager.get_session() as session:
            from sqlalchemy.future import select
            q = select(GuildSetting.setting_value).where(
                GuildSetting.guild_id == guild_id, 
                GuildSetting.setting_key == key
            )
            result = await session.execute(q)
            value = result.scalars().first()
            return value

    async def delete_setting(self, guild_id: int, key: str) -> bool:
        """
        機能名: 設定の削除 (Delete)
        """
        async with self.db_manager.get_session() as session:
            from sqlalchemy.future import select, delete
            q = select(GuildSetting).where(
                GuildSetting.guild_id == guild_id, 
                GuildSetting.setting_key == key
            )
            result = await session.execute(q)
            setting_to_delete = result.scalars().first()

            if setting_to_delete:
                await session.delete(setting_to_delete)
                return True # 削除成功
            return False # 削除対象なし

# --- 利用例 (このファイルが直接実行された場合) ---
if __name__ == "__main__":
    # このテストはプロジェクトのルートディレクトリから実行することを想定しています
    # (例: python -m utils.db_manager)
    # または、utils ディレクトリに移動して実行 (python db_manager.py)
    
    # 依存するユーティリティをインポート
    try:
        from config_manager import ConfigManager
        from bot_logger import setup_logging
    except ImportError:
        print("テスト実行エラー: 必要なモジュールが見つかりません。")
        print("プロジェクトのルートディレクトリから 'python -m utils.db_manager' として実行してみてください。")
        exit(1)

    print("--- DatabaseManager 単体テスト ---")
    
    # 1. 依存関係のセットアップ (Config, Logger)
    try:
        # 1階層上のディレクトリにあると仮定
        config_manager = ConfigManager(env_path='../.env', config_path='../config.ini')
        config = config_manager.get_config()
    except Exception as e:
        print(f"ConfigManagerの初期化に失敗: {e}")
        exit(1)
        
    logger = setup_logging(config)

    # 2. テスト用の非同期関数を定義
    async def run_db_test():
        try:
            # 3. DatabaseManager の初期化
            db_manager = DatabaseManager(config, logger)
            
            # 4. スキーマの初期化 (テーブル作成)
            await db_manager.init_db_schema()
            
            # 5. リポジトリの初期化
            repo = GuildSettingRepository(db_manager)
            
            test_guild_id = 999999
            
            # 6. CRUD テスト
            logger.info("--- CRUDテスト開始 ---")
            
            # (Create)
            logger.info("Create: 'prefix' = '!' を設定中...")
            await repo.set_setting(test_guild_id, 'prefix', '!')
            
            # (Read)
            logger.info("Read: 'prefix' を読み取り中...")
            val = await repo.get_setting(test_guild_id, 'prefix')
            assert val == '!', f"読み取り失敗: 期待値 '!', 実際の値 '{val}'"
            logger.info(f"Read成功: {val}")
            
            # (Update)
            logger.info("Update: 'prefix' = '$' に更新中...")
            await repo.set_setting(test_guild_id, 'prefix', '$')
            val_updated = await repo.get_setting(test_guild_id, 'prefix')
            assert val_updated == '$', f"更新失敗: 期待値 '$', 実際の値 '{val_updated}'"
            logger.info(f"Update成功: {val_updated}")

            # (Read - 存在しないキー)
            logger.info("Read (Miss): 'lang' を読み取り中...")
            val_none = await repo.get_setting(test_guild_id, 'lang')
            assert val_none is None, f"存在しないキーの読み取り失敗: 期待値 None, 実際の値 '{val_none}'"
            logger.info(f"Read (Miss) 成功: {val_none}")
            
            # (Delete)
            logger.info("Delete: 'prefix' を削除中...")
            deleted = await repo.delete_setting(test_guild_id, 'prefix')
            assert deleted is True, "削除失敗 (Trueが返されませんでした)"
            val_deleted = await repo.get_setting(test_guild_id, 'prefix')
            assert val_deleted is None, f"削除確認失敗: 期待値 None, 実際の値 '{val_deleted}'"
            logger.info("Delete成功")
            
            logger.info("--- CRUDテスト完了 (すべて成功) ---")

        except Exception as e:
            logger.error(f"DBテスト中にエラーが発生しました: {e}", exc_info=True)

    # 7. 非同期テストの実行
    try:
        asyncio.run(run_db_test())
    except KeyboardInterrupt:
        logger.info("テストが中断されました。")