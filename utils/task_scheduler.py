import logging
import configparser
from typing import Callable, Any

import discord
from discord.ext import commands

# 外部ライブラリ (apscheduler)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger # 👈 日時指定のためにインポート

# 依存ユーティリティ
try:
    from .config_manager import ConfigManager
    from .db_manager import DatabaseManager
    from .bot_logger import setup_logging
except ImportError:
    # 利用例の実行時に 'utils.' プレフィックスが不要な場合
    from config_manager import ConfigManager
    from db_manager import DatabaseManager, Base # 利用例のために Base もインポート
    from bot_logger import setup_logging

class TaskScheduler:
    """
    非同期タスク (cron, interval, date) を管理するクラス。
    ジョブストア (DB) を使用してタスクの永続化に対応します。
    """

    def __init__(
        self, 
        bot: commands.Bot, 
        config_manager: ConfigManager, 
        db_manager: DatabaseManager, 
        logger: logging.Logger
    ):
        """
        機能名: コンストラクタ
        説明: スケジューラを初期化し、DBジョブストアを設定します。
        引数:
            bot (commands.Bot): Discord ボットのインスタンス
            config_manager (ConfigManager): 設定マネージャー
            db_manager (DatabaseManager): DBマネージャー (永続化のため)
            logger (logging.Logger): ロガー
        戻り値: なし
        """
        self.bot = bot
        self.config = config_manager.get_config()
        self.db_manager = db_manager
        self.logger = logger
        
        try:
            db_type = self.config.get('Database', 'Type', fallback='sqlite')
            db_dsn = self.config.get('Database', 'DSN', fallback='db/bot.db')
            
            # db_manager が持つDSN構築ロジックを利用
            full_dsn = db_manager._build_dsn(db_type, db_dsn)

            jobstores = {
                'default': SQLAlchemyJobStore(url=full_dsn)
            }
            
            # discord.py のイベントループと統合
            self.scheduler = AsyncIOScheduler(jobstores=jobstores)
            
            self.logger.info("TaskScheduler が初期化されました。ジョブストア (DB) を使用します。")

        except Exception as e:
            self.logger.critical(f"TaskScheduler の初期化に失敗しました: {e}", exc_info=True)
            raise

    def start(self):
        """
        機能名: スケジューラ開始
        説明: スケジューラの実行を開始します。ボットの on_ready 後に呼び出します。
        引数: なし
        戻り値: なし
        """
        try:
            self.scheduler.start()
            self.logger.info("TaskScheduler が開始されました。")
        except Exception as e:
            self.logger.error(f"TaskScheduler の開始に失敗: {e}", exc_info=True)

    def shutdown(self):
        """
        機能名: スケジューラ停止
        説明: スケジューラを安全に停止します。ボットのシャットダウン時に呼び出します。
        引数: なし
        戻り値: なし
        """
        try:
            self.scheduler.shutdown(wait=False) # 非同期ループ内では wait=False を推奨
            self.logger.info("TaskScheduler が停止しました。")
        except Exception as e:
            self.logger.error(f"TaskScheduler の停止中にエラー: {e}", exc_info=True)

    def add_task(
        self, 
        func: Callable[..., Any], 
        trigger: str,
        task_id: str,
        **trigger_args
    ):
        """
        機能名: タスクの追加
        説明:
            新しいタスクをスケジュールに登録します。
            タスク関数 (func) には、bot や db_manager を渡すことができます。
        引数:
            func (Callable): 実行する非同期関数 (async def ...)
            trigger (str): 'cron', 'interval', または 'date'
            task_id (str): タスクの一意なID (DB保存時に使用)
            **trigger_args: 
                cronの場合: (例) hour=9, minute=0, timezone='Asia/Tokyo'
                intervalの場合: (例) weeks=0, days=0, hours=1, minutes=0, seconds=30
                dateの場合: (例) run_date='2025-12-25 09:30:00' (ISO 8601) または datetimeオブジェクト
        戻り値: なし
        """
        
        # タスク関数 (func) に渡す引数を準備
        task_kwargs = {
            'bot': self.bot,
            'db_manager': self.db_manager,
            'logger': self.logger
        }
        
        trigger_instance = None
        if trigger == 'cron':
            trigger_instance = CronTrigger(**trigger_args)
        elif trigger == 'interval':
            trigger_instance = IntervalTrigger(**trigger_args)
        elif trigger == 'date':
            # 👈 'date' トリガー (日時指定) に対応
            trigger_instance = DateTrigger(**trigger_args)
        else:
            self.logger.error(f"未知のトリガータイプ: {trigger}")
            return

        try:
            # ジョブを登録
            self.scheduler.add_job(
                func,
                trigger=trigger_instance,
                id=task_id,
                kwargs=task_kwargs,
                replace_existing=True, # 既にDBに同IDのジョブがあっても設定を上書き
                misfire_grace_time=300 # 実行遅延の許容時間 (秒)
            )
            self.logger.info(f"タスク '{task_id}' ({trigger}) をスケジュールに追加しました。引数: {trigger_args}")
            
        except Exception as e:
            self.logger.error(f"タスク '{task_id}' の追加に失敗しました: {e}", exc_info=True)

    def remove_task(self, task_id: str):
        """
        機能名: タスクの削除
        説明: スケジュールからタスクを削除します。
        引数:
            task_id (str): 削除するタスクのID
        戻り値: なし
        """
        try:
            self.scheduler.remove_job(task_id)
            self.logger.info(f"タスク '{task_id}' を削除しました。")
        except Exception as e:
            self.logger.warning(f"タスク '{task_id}' の削除に失敗しました (存在しない可能性あり): {e}")

# --- 利用例 (このファイルが直接実行された場合) ---

# 利用例のためのテスト用非同期関数
async def example_cron_task(bot: commands.Bot, db_manager: DatabaseManager, logger: logging.Logger):
    """(テスト用) cronタスク"""
    logger.info("--- [Task Executed] example_cron_task 実行 ---")

async def example_interval_task(bot: commands.Bot, db_manager: DatabaseManager, logger: logging.Logger):
    """(テスト用) 5秒ごとに実行されるタスク"""
    logger.info("--- [Task Executed] example_interval_task 実行 (5秒ごと) ---")

async def example_date_task(bot: commands.Bot, db_manager: DatabaseManager, logger: logging.Logger):
    """(テスト用) 1回だけ実行されるタスク"""
    logger.info("--- [Task Executed] example_date_task 実行 (1回限り) ---")


if __name__ == "__main__":
    import asyncio
    import os
    import datetime # 👈 日時指定テストのためにインポート

    print("--- TaskScheduler 単体テスト (日時指定機能追加版) ---")
    
    # 1. 依存関係のセットアップ (Config, Logger, DB)
    try:
        # 1階層上のディレクトリにあると仮定
        ini_path = '../config.ini'
        env_path = '../.env'
        
        # テスト用のダミー config.ini を作成 (DB設定を確実に)
        if not os.path.exists(ini_path):
            ini_path = 'temp_test_config.ini'
            with open(ini_path, 'w', encoding='utf-f8') as f:
                f.write("""
[Logging]
LogLevel = DEBUG
[Database]
Type = sqlite
DSN = db/test_scheduler.db
                """)
            print(f"'{ini_path}' をテスト用に作成しました。")
            
        if not os.path.exists(env_path):
            env_path = 'temp_test.env'
            with open(env_path, 'w', encoding='utf-8') as f:
                f.write('DISCORD_BOT_TOKEN="test_token"\n')
            print(f"'{env_path}' をテスト用に作成しました。")

        config_manager = ConfigManager(env_path=env_path, config_path=ini_path)
        config = config_manager.get_config()
        logger = setup_logging(config)
        db_manager = DatabaseManager(config, logger)

    except Exception as e:
        print(f"依存関係の初期化に失敗: {e}")
        exit(1)

    # 2. テスト用の最小限の Bot インスタンスを作成
    intents = discord.Intents.default()
    mock_bot = commands.Bot(command_prefix="!", intents=intents)

    # 3. テスト用の非同期関数を定義
    async def run_scheduler_test():
        logger.info("--- スケジューラテスト開始 ---")
        
        # 4. (必須) DBスキーマを初期化 (apscheduler がテーブルを作成するため)
        await db_manager.init_db_schema() 
        
        # 5. スケジューラの初期化
        scheduler = TaskScheduler(mock_bot, config_manager, db_manager, logger)
        
        # 6. タスクの登録
        
        # (a) 5秒ごとのタスク
        scheduler.add_task(
            func=example_interval_task,
            trigger='interval',
            task_id='interval_5sec_test',
            seconds=5
        )
        
        # (b) 日時指定タスク (今から3秒後に実行)
        run_at_time = datetime.datetime.now() + datetime.timedelta(seconds=3)
        scheduler.add_task(
            func=example_date_task,
            trigger='date',
            task_id='date_test_3sec',
            run_date=run_at_time
        )
        logger.info(f"日時指定タスクを {run_at_time} に設定しました。")

        # 7. スケジューラ開始
        scheduler.start()
        
        # 8. 12秒間待機
        logger.info(f"--- 12秒間、タスクの実行を待機します... (3秒後に 'date' が、5秒後, 10秒後に 'interval' が実行されるはず) ---")
        await asyncio.sleep(12)
        
        # 9. シャットダウン
        scheduler.shutdown()
        logger.info("--- スケジューラテスト終了 ---")

    # 10. 非同期テストの実行
    try:
        asyncio.run(run_scheduler_test())
    except KeyboardInterrupt:
        logger.info("テストが中断されました。")
    finally:
        # テスト用に作成したファイルを削除
        if 'temp_test' in ini_path:
            os.remove(ini_path)
            print(f"'{ini_path}' を削除しました。")
        if 'temp_test' in env_path:
            os.remove(env_path)
            print(f"'{env_path}' を削除しました。")
        
        db_file = config.get('Database', 'DSN')
        if 'test_scheduler.db' in db_file and os.path.exists(db_file):
            os.remove(db_file)
            print(f"'{db_file}' を削除しました。")
            db_dir = os.path.dirname(db_file)
            if os.path.exists(db_dir) and not os.listdir(db_dir):
                os.rmdir(db_dir)
                print(f"'{db_dir}' を削除しました。")