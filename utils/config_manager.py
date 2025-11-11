import os
import configparser
from dotenv import load_dotenv

class ConfigManager:
    """
    ボットの設定を管理するクラス。
    .envファイルから機密情報を、config.iniから一般設定を読み込みます。
    """

    def __init__(self, env_path='.env', config_path='config.ini'):
        """
        機能名: コンストラクタ
        説明: 設定ファイルを読み込み、クラスを初期化します。
        引数:
            env_path (str): .envファイルのパス
            config_path (str): config.iniファイルのパス
        戻り値: なし
        """
        self.env_path = env_path
        self.config_path = config_path
        
        # 機密情報を保持する変数
        self.discord_bot_token: str = ""
        
        # config.ini の設定を保持するパーサー
        self.config = configparser.ConfigParser()
        
        # 設定の読み込みを実行
        self._load_env()
        self._load_ini()

    def _load_env(self):
        """
        機能名: 環境変数の読み込み (.env)
        説明: .envファイルから機密情報（トークンなど）を読み込み、クラス変数に格納します。
        引数: なし
        戻り値: なし
        """
        load_dotenv(self.env_path)
        
        token = os.getenv('DISCORD_BOT_TOKEN')
        if not token:
            # トークンが見つからない場合は起動できないため、例外を発生させる
            raise ValueError(f"DISCORD_BOT_TOKENが'{self.env_path}'に見つかりません。")
        
        self.discord_bot_token = token
        # print("DEBUG: .envの読み込みに成功しました。") # デバッグ用

    def _load_ini(self):
        """
        機能名: 設定ファイルの読み込み (config.ini)
        説明: config.iniファイルから一般設定を読み込みます。
        引数: なし
        戻り値: なし
        """
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"設定ファイル '{self.config_path}' が見つかりません。")
            
        self.config.read(self.config_path, encoding='utf-8')
        # print("DEBUG: config.iniの読み込みに成功しました。") # デバッグ用

    def get_config(self) -> configparser.ConfigParser:
        """
        機能名: ConfigParserインスタンスの取得
        説明: 読み込まれたconfig.iniの内容を持つConfigParserインスタンスを返します。
        引数: なし
        戻り値:
            configparser.ConfigParser: 読み込まれた設定
        """
        return self.config

    def get_token(self) -> str:
        """
        機能名: Discordボットトークンの取得
        説明: 読み込まれたDiscordボットトークンを返します。
        引数: なし
        戻り値:
            str: Discordボットトークン
        """
        return self.discord_bot_token

    def get_guild_setting(self, guild_id: int, key: str) -> str | None:
        """
        機能名: サーバー固有設定の取得
        説明: 
            指定されたサーバーIDとキーに基づき、config.iniから固有設定を取得します。
            固有設定が存在しない場合、[BotSettings]のデフォルト値を参照します。
        引数:
            guild_id (int): サーバー(ギルド)のID
            key (str): 取得したい設定のキー (例: Prefix)
        戻り値:
            str | None: 設定値。見つからない場合はNone。
        """
        section_name = f"Guild_{guild_id}"
        
        # サーバー固有のセクションにキーが存在するか確認
        if section_name in self.config and key in self.config[section_name]:
            return self.config[section_name].get(key)
        
        # 固有設定がなければ、デフォルト設定 ([BotSettings]) を確認
        if 'BotSettings' in self.config and key in self.config['BotSettings']:
            return self.config['BotSettings'].get(key)
            
        # デフォルトにも存在しない場合
        return None



# --- 利用例 (このファイルが直接実行された場合) ---
if __name__ == "__main__":
    # プロジェクトルートに .env と config.ini があると仮定してテスト
    # 実際には main.py から呼び出す
    try:
        # 1階層上のディレクトリにあると仮定
        config_manager = ConfigManager(env_path='../.env', config_path='../config.ini')
        
        # トークンの取得
        print(f"Bot Token (最初の5文字): {config_manager.get_token()[:5]}...")
        
        # 一般設定の取得
        config = config_manager.get_config()
        print(f"Log Level: {config.get('Logging', 'LogLevel')}")
        print(f"DB Type: {config.get('Database', 'Type')}")
        print(f"Default Prefix: {config.get('BotSettings', 'DefaultPrefix')}")

        # サーバー固有設定の取得 (サンプルのIDを使用)
        test_guild_id = 123456789012345678
        prefix = config_manager.get_guild_setting(test_guild_id, 'Prefix')
        print(f"Guild {test_guild_id} の Prefix: {prefix}")
        
        test_guild_id_2 = 876543210987654321
        prefix_2 = config_manager.get_guild_setting(test_guild_id_2, 'Prefix')
        print(f"Guild {test_guild_id_2} の Prefix (デフォルト): {prefix_2}")

    except (ValueError, FileNotFoundError) as e:
        print(f"エラー: {e}")