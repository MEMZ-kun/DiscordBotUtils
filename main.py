import discord
from discord.ext import commands
import asyncio
import os
import logging # 標準ロギング

# ----------------------------------------------------
# 1. ユーティリティのインポート
# ----------------------------------------------------
# 各ユーティリティが依存関係の順に並んでいます
from utils.config_manager import ConfigManager
from utils.bot_logger import setup_logging, log_event, log_error
from utils.db_manager import DatabaseManager, GuildSettingRepository, Base
from utils.error_handler import ErrorHandler
from utils.permission_handler import PermissionManager
from utils.task_scheduler import TaskScheduler

# ----------------------------------------------------
# 2. メイン実行関数
# ----------------------------------------------------

async def main():
    """
    ボットの初期化と実行を制御するメイン関数
    """

    # --- ステップ 1: ConfigManager の初期化 ---
    # 最も重要。トークンや *すべて* の設定ファイルを読み込みます。
    # これが失敗するとボットは起動できません。
    try:
        config_manager = ConfigManager(env_path='.env', config_path='config.ini')
        BOT_TOKEN = config_manager.get_token()
        config = config_manager.get_config()
    except (FileNotFoundError, ValueError) as e:
        print(f"[緊急エラー] 設定ファイルの読み込みに失敗しました: {e}")
        print("'.env' と 'config.ini' がプロジェクトルートに存在するか確認してください。")
        return # 起動中止

    # --- ステップ 2: Logger の初期化 ---
    # Config に基づいて、ファイルとコンソールへのログ出力を設定します。
    # これ以降、'print' の代わりに 'logger.info' などを使用します。
    logger = setup_logging(config)

    # --- ステップ 3: DatabaseManager の初期化 ---
    # Config に基づき、指定されたDB (SQLite, PostgreSQL等) への接続を準備します。
    # (この時点ではまだ接続せず、エンジンを作成するだけです)
    try:
        db_manager = DatabaseManager(config, logger)
        # DB操作(CRUD)の窓口となるリポジトリを初期化
        guild_repo = GuildSettingRepository(db_manager)
    except Exception as e:
        logger.critical(f"DatabaseManager の初期化に失敗しました: {e}", exc_info=True)
        return # 起動中止

    # --- ステップ 4: Discord Bot インスタンスの作成 ---
    # Botの動作に必要な「権限 (Intents)」を定義します。
    #必要なものだけを有効化します。
    intents = discord.Intents.default()
    intents.message_content = True  # プレフィックスコマンドやメッセージ内容の読み取りに必要
    intents.members = True          # メンバーの参加/退出イベントやロール管理に必要
    intents.guilds = True           # サーバー情報の取得に必要

    # Botインスタンスを作成
    bot = commands.Bot(
        command_prefix=commands.when_mentioned_or('!'), # デフォルト (Cogsで上書き可能)
        intents=intents,
        help_command=None # 標準のhelpを無効化 (独自に作成するため)
    )

    # --- ステップ 5: ユーティリティの Bot へのアタッチ ---
    # これにより、Cogs (機能拡張) から 'bot.logger' や 'bot.db_manager' として
    # ユーティリティにアクセスできるようになります。
    bot.logger = logger
    bot.config_manager = config_manager
    bot.db_manager = db_manager
    bot.guild_repo = guild_repo # DB操作リポジトリ

    # --- ステップ 6: Permission / Task / Error ハンドラの初期化 ---
    # Botインスタンスや他のユーティリティを渡して初期化します。
    
    # 権限マネージャー (Cogsがコマンドの権限チェックに使う)
    bot.permission_manager = PermissionManager(config_manager, logger)
    
    # タスクスケジューラ (DBにタスクを保存)
    task_scheduler = TaskScheduler(bot, config_manager, db_manager, logger)
    
    # エラーハンドラ (Bot全体のエラーを監視)
    error_handler = ErrorHandler(logger, config_manager)

    # --- ステップ 7: Cog (機能拡張) のロード ---
    # 'cogs' ディレクトリ内の .py ファイルをすべて読み込みます。
    # 独自機能は Cogs として追加していきます。
    try:
        cogs_dir = 'cogs'
        if not os.path.exists(cogs_dir):
            os.makedirs(cogs_dir)
            logger.info(f"'{cogs_dir}' ディレクトリを作成しました。")
            
        for filename in os.listdir(cogs_dir):
            if filename.endswith('.py') and not filename.startswith('__'):
                cog_name = f"{cogs_dir}.{filename[:-3]}"
                await bot.load_extension(cog_name)
                logger.info(f"Cog '{cog_name}' をロードしました。")
                
    except Exception as e:
        logger.error(f"Cog のロード中にエラーが発生しました: {e}", exc_info=True)

    # --- ステップ 8: Bot イベントリスナーの登録 ---

    @bot.event
    async def on_ready():
        """ボットがDiscordへの接続と準備が完了したときに呼び出されます。"""
        logger.info(f"ボットが {bot.user} (ID: {bot.user.id}) としてログインしました。")
        logger.info(f"所属サーバー数: {len(bot.guilds)}")
        
        # (重要) データベースのテーブルを作成・確認します
        # (apscheduler のテーブルもここで作成されます)
        await db_manager.init_db_schema()

        # (重要) タスクスケジューラを開始します
        # (DBに保存されたタスクを読み込み、実行を開始します)
        task_scheduler.start()

        # スラッシュコマンドをDiscordサーバーに同期 (反映) します
        try:
            synced = await bot.tree.sync()
            logger.info(f"{len(synced)} 個のスラッシュコマンドを同期しました。")
        except Exception as e:
            logger.error(f"スラッシュコマンドの同期に失敗しました: {e}", exc_info=True)

    @bot.event
    async def on_guild_join(guild: discord.Guild):
        """ボットが新しいサーバーに招待されたときに呼び出されます。"""
        log_event(logger, f"新しいサーバーに参加しました: {guild.name} (ID: {guild.id})")

    # --- ステップ 9: エラーハンドラの登録 ---

    @bot.event
    async def on_command_error(ctx: commands.Context, error: commands.CommandError):
        """プレフィックスコマンドでエラーが発生したときに呼び出されます。"""
        # 汎用エラーハンドラに処理を委任
        await error_handler.process_command_error(ctx, error)

    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        """スラッシュコマンドでエラーが発生したときに呼び出されます。"""
        # 汎用エラーハンドラに処理を委任
        await error_handler.process_command_error(interaction, error)

    # --- ステップ 10: ボットの起動 ---
    try:
        logger.info("ボットを起動します...")
        await bot.start(BOT_TOKEN)
        
    except discord.errors.LoginFailure:
        logger.critical("ボットトークンが無効です。'.env' ファイルを確認してください。")
    except discord.errors.PrivilegedIntentsRequired:
        logger.critical("必要な Intents (特に Members) が有効になっていません。")
        logger.critical("Discord Developer Portal で 'SERVER MEMBERS INTENT' をオンにしてください。")
    except Exception as e:
        logger.critical(f"ボットの起動中に予期せぬエラーが発生しました: {e}", exc_info=True)
    finally:
        # ボットが停止した (Ctrl+C など) 場合のクリーンアップ
        if not bot.is_closed():
            await bot.close()
        task_scheduler.shutdown()
        logger.info("ボットがシャットダウンしました。")


# ----------------------------------------------------
# 3. プログラムのエントリーポイント
# ----------------------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[情報] ユーザーによってボットが停止されました。")