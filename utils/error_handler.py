import logging
import configparser
import discord
from discord.ext import commands
from discord import app_commands

# ユーティリティから必要なコンポーネントをインポート
# bot_logger からは log_error 関数のみをインポート
try:
    from .bot_logger import log_error, setup_logging
    from .config_manager import ConfigManager
except ImportError:
    # 利用例の実行時に 'utils.' プレフィックスが不要な場合
    from bot_logger import log_error, setup_logging
    from config_manager import ConfigManager


# --- カスタム例外 ---

class ExternalAPIError(Exception):
    """
    外部API(例: 天気、株価など)への接続エラーを示すカスタム例外。
    ボットの独自機能で raise されることを想定。
    """
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


class ErrorHandler:
    """
    ボット全体のエラーを処理するクラス。
    プレフィックスコマンドとスラッシュコマンドの両方に対応します。
    """

    def __init__(self, logger: logging.Logger, config_manager: ConfigManager):
        """
        機能名: コンストラクタ
        説明: ロガーと設定マネージャーのインスタンスを初期化時に受け取ります。
        引数:
            logger (logging.Logger): bot_logger でセットアップされたロガー
            config_manager (ConfigManager): config_manager のインスタンス
        戻り値: なし
        """
        self.logger = logger
        self.config = config_manager.get_config()
        
        try:
            # config.ini から [Logging] > NotifyErrorToDiscord の設定を読み込む
            self.notify_user_on_error = self.config.getboolean(
                'Logging', 
                'NotifyErrorToDiscord', 
                fallback=False
            )
        except configparser.Error as e:
            self.logger.warning(f"config.ini から NotifyErrorToDiscord の読み取りに失敗: {e}")
            self.notify_user_on_error = False

    async def _send_error_message(
        self, 
        ctx_or_interaction: commands.Context | discord.Interaction, 
        message: str,
        ephemeral: bool = True
    ):
        """
        機能名: (内部) エラーメッセージ送信ヘルパー
        説明:
            コマンド実行者（ユーザー）にエラーメッセージを返信します。
            Context (プレフィックス) と Interaction (スラッシュ) の両方に対応します。
        引数:
            ctx_or_interaction (Context | Interaction): コマンドのコンテキスト
            message (str): ユーザーに表示するエラーメッセージ
            ephemeral (bool): Trueの場合、メッセージは本人にのみ表示されます
        戻り値: なし
        """
        
        # --- 単体テスト用のモック処理 ---
        if isinstance(ctx_or_interaction, MockContext):
            print(f"--- [テスト] ユーザーへの返信 (ephemeral={ephemeral}): {message}")
            return
        # --- ここまでモック処理 ---

        try:
            if isinstance(ctx_or_interaction, commands.Context):
                await ctx_or_interaction.reply(message, ephemeral=ephemeral)
            
            elif isinstance(ctx_or_interaction, discord.Interaction):
                if ctx_or_interaction.is_response_sent():
                    await ctx_or_interaction.followup.send(message, ephemeral=ephemeral)
                else:
                    await ctx_or_interaction.response.send_message(message, ephemeral=ephemeral)
        
        except discord.errors.Forbidden:
            log_error(self.logger, None, "エラーメッセージの送信に必要な権限がありません (Forbidden)。", exc_info=False)
        except Exception as e:
            log_error(self.logger, e, "エラーメッセージの送信処理中に別のエラーが発生しました。")


    async def process_command_error(
        self, 
        ctx_or_interaction: commands.Context | discord.Interaction, 
        error: Exception
    ):
        """
        機能名: 汎用コマンドエラー処理
        説明:
            プレフィックス(on_command_error)とスラッシュ(tree.on_error)の両方から
            呼び出される共通のエラー処理ロジック。
        引数:
            ctx_or_interaction (Context | Interaction): コマンドのコンテキスト
            error (Exception): 発生した例外オブジェクト
        戻り値: なし
        """

        original_error = getattr(error, 'original', error)
        
        # --- 1. ユーザーに起因する、ログ不要のサイレントなエラー ---
        if isinstance(original_error, (commands.CommandNotFound, app_commands.CommandNotFound)):
            return

        if isinstance(original_error, (commands.CheckFailure, app_commands.CheckFailure)):
            await self._send_error_message(ctx_or_interaction, "あなたはこのコマンドを実行する権限がありません。")
            return

        # --- 2. ユーザーの入力ミス (ログには記録するが、スタックトレースは不要) ---
        if isinstance(original_error, (commands.MissingRequiredArgument, commands.BadArgument)):
            self.logger.warning(f"[Usage Error] {ctx_or_interaction.author}: {original_error}")
            await self._send_error_message(ctx_or_interaction, f"コマンドの使い方が間違っています。\n```\n{original_error}\n```")
            return

        # --- 3. Discord API または 外部APIのエラー (警告として記録) ---
        if isinstance(original_error, discord.errors.RateLimited):
            self.logger.warning(f"Discord APIのレートリミットに達しました。{original_error.retry_after:.2f}秒待機します。")
            return

        if isinstance(original_error, discord.errors.Forbidden):
            missing_perms = getattr(original_error, 'missing_perms', '不明')
            log_error(self.logger, original_error, 
                      f"Discord APIエラー (403 Forbidden): ボットに必要な権限がありません。不足権限: {missing_perms}", 
                      exc_info=False)
            await self._send_error_message(ctx_or_interaction, "ボットの権限が不足しています。サーバー管理者に連絡してください。")
            return
            
        if isinstance(original_error, ExternalAPIError):
            self.logger.warning(f"[External API Error] {original_error.message}")
            await self._send_error_message(ctx_or_interaction, f"外部APIへの接続に失敗しました: {original_error.message}")
            return

        # --- 4. 予期せぬその他のエラー (スタックトレース付きで記録) ---
        log_error(
            self.logger, 
            original_error, 
            message=f"コマンド実行中に予期せぬエラーが発生しました。",
            exc_info=True
        )

        if self.notify_user_on_error:
            await self._send_error_message(
                ctx_or_interaction,
                "予期せぬエラーが発生しました。管理者に通知されました。"
            )


# --- 利用例 (このファイルが直接実行された場合) ---

# テストのために、discord.Context や discord.User のダミー（モック）を作成します
class MockAuthor:
    def __init__(self, name="TestUser", id=12345):
        self.name = name
        self.id = id
    def __str__(self):
        return f"{self.name}#{self.id}"

class MockContext:
    def __init__(self, author=None):
        self.author = author if author else MockAuthor()

if __name__ == "__main__":
    import asyncio

    print("--- ErrorHandler 単体テスト ---")

    # 1. 依存関係のセットアップ (Config, Logger)
    try:
        config_manager = ConfigManager(env_path='../.env', config_path='../config.ini')
        config = config_manager.get_config()
    except Exception as e:
        print(f"ConfigManagerの初期化に失敗: {e}")
        exit(1)
        
    logger = setup_logging(config)
    
    # config.ini の [Logging] > NotifyErrorToDiscord の設定をテスト用に強制上書き
    config_manager.config['Logging']['NotifyErrorToDiscord'] = 'True'
    logger.info("テストのため、'NotifyErrorToDiscord' を True に設定しました。")
    
    # 2. ErrorHandler の初期化
    handler = ErrorHandler(logger, config_manager)
    
    # 3. モック（ダミー）のコンテキストを作成
    mock_ctx = MockContext()

    # 4. テスト用の非同期関数
    async def run_error_test():
        print("\n--- [テストケース1] 権限エラー (CheckFailure) ---")
        # ユーザーに「権限がありません」と通知され、ログには何も出ないはず
        await handler.process_command_error(mock_ctx, commands.CheckFailure())

        print("\n--- [テストケース2] 引数エラー (BadArgument) ---")
        # ユーザーに「使い方が間違っています」と通知され、ログに Warning が出るはず
        await handler.process_command_error(mock_ctx, commands.BadArgument("引数の型が違います。"))
        
        print("\n--- [テストケース3] 外部APIエラー (ExternalAPIError) ---")
        # ユーザーに「外部APIエラー」が通知され、ログに Warning が出るはず
        await handler.process_command_error(mock_ctx, ExternalAPIError("天気情報の取得に失敗"))

        print("\n--- [テストケース4] 予期せぬエラー (ZeroDivisionError) ---")
        # ユーザーに「予期せぬエラー」と通知され、ログに Error (スタックトレース付き) が出るはず
        try:
            1 / 0
        except ZeroDivisionError as e:
            await handler.process_command_error(mock_ctx, e)

    # 5. 非同期テストの実行
    try:
        asyncio.run(run_error_test())
        print("\n--- ErrorHandler テスト完了 ---")
        print(f"詳細は {config.get('Logging', 'LogFile')} を確認してください。")
    except KeyboardInterrupt:
        logger.info("テストが中断されました。")