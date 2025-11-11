import logging
import os
from logging.handlers import RotatingFileHandler
import configparser
import sys
import discord # discord.py の型ヒントのためにインポート

# ログレベルの文字列を logging モジュールの定数に変換する
LOG_LEVEL_MAP = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARN': logging.WARNING,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
}

def setup_logging(config: configparser.ConfigParser) -> logging.Logger:
    """
    機能名: ロギングのセットアップ
    説明:
        config.ini の [Logging] セクションに基づき、ロガーをセットアップします。
        ファイルハンドラ (ローテーション機能付き) とコンソールハンドラの両方を追加します。
    引数:
        config (configparser.ConfigParser): ConfigManagerから取得した設定オブジェクト
    戻り値:
        logging.Logger: セットアップ済みの 'bot' ロガーインスタンス
    """
    try:
        # iniから設定を読み込む (存在しない場合のデフォルト値も指定)
        log_level_str = config.get('Logging', 'LogLevel', fallback='INFO')
        log_file_path = config.get('Logging', 'LogFile', fallback='logs/bot.log')
        max_bytes = config.getint('Logging', 'LogMaxBytes', fallback=10485760) # 10MB
        backup_count = config.getint('Logging', 'LogBackupCount', fallback=5)
        
    except configparser.Error as e:
        print(f"config.ini の読み取りエラー: {e}")
        # フォールバック値で続行
        log_level_str = 'INFO'
        log_file_path = 'logs/bot.log'
        max_bytes = 10485760
        backup_count = 5

    # ログレベルを変換
    log_level = LOG_LEVEL_MAP.get(log_level_str.upper(), logging.INFO)

    # 'bot' という名前のロガーを取得 (ライブラリのログと分離するため)
    logger = logging.getLogger('bot')
    logger.setLevel(log_level)
    
    # 既にハンドラが設定されている場合、二重に追加しないようにする
    if logger.hasHandlers():
        logger.handlers.clear()

    # ログフォーマット
    formatter = logging.Formatter(
        '%(asctime)s - [%(levelname)-7s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 1. ファイルハンドラ (ローテーション機能付き)
    try:
        # ログファイルのディレクトリが存在するか確認・作成
        log_dir = os.path.dirname(log_file_path)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
    except (IOError, PermissionError) as e:
        print(f"[エラー] ログファイル '{log_file_path}' への書き込みに失敗しました: {e}")

    # 2. コンソール (標準出力) ハンドラ
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level) # コンソールもiniのレベルに従う
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logger.info("ロガーのセットアップが完了しました。")
    return logger

# --- ヘルパー関数 ---
# これらは main.py や他のモジュールから呼び出されることを想定しています

def log_command(logger: logging.Logger, ctx: discord.Message | discord.Interaction):
    """
    機能名: コマンド実行ログ
    説明: コマンドが実行された際の標準的なログを記録します。
    引数:
        logger (logging.Logger): ロガーインスタンス
        ctx (discord.Message | discord.Interaction): コマンドのコンテキスト
    戻り値: なし
    """
    if isinstance(ctx, discord.Message):
        # プレフィックスコマンドの場合
        author = ctx.author
        guild = ctx.guild
        channel = ctx.channel
        content = ctx.content
    elif isinstance(ctx, discord.Interaction):
        # スラッシュコマンドの場合
        author = ctx.user
        guild = ctx.guild
        channel = ctx.channel
        content = f"/{ctx.command.name}" # TODO: 引数を取得する (v2.0以降)
    else:
        logger.warn("未知のコンテキストタイプがlog_commandに渡されました。")
        return

    guild_name = guild.name if guild else "DM"
    channel_name = channel.name if hasattr(channel, 'name') else f"ID: {channel.id}"
    
    logger.info(
        f"[Cmd] {guild_name} > #{channel_name} | {author} (ID: {author.id}) | {content}"
    )

def log_event(logger: logging.Logger, message: str, guild: discord.Guild = None):
    """
    機能名: イベントログ
    説明: メンバーの参加/退出など、汎用的なイベントを記録します。
    引数:
        logger (logging.Logger): ロガーインスタンス
        message (str): 記録するメッセージ (例: "メンバーが参加しました: ...")
        guild (discord.Guild, optional): イベントが発生したサーバー
    戻り値: なし
    """
    guild_name = f"[{guild.name}] " if guild else ""
    logger.info(f"[Evt] {guild_name}{message}")


def log_error(logger: logging.Logger, error: Exception, message: str = "エラーが発生しました。", exc_info: bool = True):
    """
    機能名: エラーログ
    説明: 予期せぬエラーや例外を、スタックトレース付きで記録します。
    引数:
        logger (logging.Logger): ロガーインスタンス
        error (Exception): 発生した例外オブジェクト
        message (str): エラーの概要メッセージ
        exc_info (bool): スタックトレースをログに出力するかどうか
    戻り値: なし
    """
    logger.error(
        f"[Err] {message} - Type: {type(error).__name__} - Details: {error}",
        exc_info=exc_info # Trueの場合、スタックトレースがログに出力される
    )


# --- 利用例 (このファイルが直接実行された場合) ---
if __name__ == "__main__":
    # utils.config_managerからConfigManagerをインポート
    from config_manager import ConfigManager
    
    print("ロガーの単体テストを開始します...")
    
    try:
        # 1階層上のディレクトリにあると仮定
        config_manager = ConfigManager(env_path='../.env', config_path='../config.ini')
        config = config_manager.get_config()
        
        # ロガーをセットアップ
        test_logger = setup_logging(config)
        
        # 各種ログのテスト
        test_logger.debug("これはDEBUGレベルのログです。")
        test_logger.info("これはINFOレベルのログです。")
        test_logger.warning("これはWARNINGレベルのログです。")
        test_logger.error("これはERRORレベルのログです。")
        
        log_event(test_logger, "テストイベントが発生しました。")
        
        # エラーログのテスト
        try:
            1 / 0
        except ZeroDivisionError as e:
            log_error(test_logger, e, message="ゼロ除算エラーのテスト")

        print("\nテスト完了。")
        print(f"ログファイルは '{config.get('Logging', 'LogFile')}' を確認してください。")

    except Exception as e:
        print(f"ロガーのテスト中に予期せぬエラーが発生しました: {e}")