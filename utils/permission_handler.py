import configparser
import logging
import discord
from discord.ext import commands
from discord import app_commands

# 依存ユーティリティ
try:
    from .config_manager import ConfigManager
    from .bot_logger import setup_logging
except ImportError:
    # 利用例の実行時に 'utils.' プレフィックスが不要な場合
    from config_manager import ConfigManager
    from bot_logger import setup_logging


def _parse_list_from_ini(config_str: str) -> list[str]:
    """iniから読み取ったカンマ区切りの文字列をリストに変換するヘルパー"""
    if not config_str:
        return []
    return [item.strip() for item in config_str.split(',') if item.strip()]

class PermissionManager:
    """
    ボットの権限（ロール、ユーザーID）を管理し、
    コマンドや機能の実行可否を判定するクラス。
    """

    def __init__(self, config_manager: ConfigManager, logger: logging.Logger):
        """
        機能名: コンストラクタ
        説明: config.ini から権限設定を読み込みます。
        引数:
            config_manager (ConfigManager): ConfigManager のインスタンス
            logger (logging.Logger): ロガーインスタンス
        戻り値: なし
        """
        self.config = config_manager.get_config()
        self.logger = logger
        
        # ボット全体の管理者設定
        self.admin_role_names: list[str] = []
        self.admin_user_ids: list[int] = []
        
        try:
            admin_roles_str = self.config.get('Permissions', 'AdminRoles', fallback='')
            admin_users_str = self.config.get('Permissions', 'AdminUsers', fallback='')
            
            self.admin_role_names = _parse_list_from_ini(admin_roles_str)
            self.admin_user_ids = [int(uid) for uid in _parse_list_from_ini(admin_users_str) if uid.isdigit()]
            
            logger.info(f"管理者ロールを読み込みました: {self.admin_role_names}")
            logger.info(f"管理者ユーザーIDを読み込みました: {self.admin_user_ids}")

        except Exception as e:
            logger.error(f"[Permissions] セクションの読み込みに失敗しました: {e}", exc_info=True)

    def _get_ctx_or_interaction_member(
        self, 
        ctx_or_interaction: commands.Context | discord.Interaction
    ) -> discord.Member | discord.User | None:
        """(内部) ContextまたはInteractionから実行ユーザー(Member/User)を取得する"""
        if isinstance(ctx_or_interaction, commands.Context):
            return ctx_or_interaction.author
        elif isinstance(ctx_or_interaction, discord.Interaction):
            return ctx_or_interaction.user
        return None

    def is_bot_admin(self, member: discord.Member | discord.User) -> bool:
        """
        機能名: ボット管理者判定
        説明:
            指定されたユーザーが、config.ini で定義された「ボット管理者」
            (AdminRoles または AdminUsers) かどうかを判定します。
            コマンド以外の汎用的な機能チェックに使用できます。
        引数:
            member (discord.Member | discord.User): チェック対象のユーザー
        戻り値:
            bool: ボット管理者の場合は True
        """
        if not isinstance(member, (discord.Member, discord.User)):
            return False # 不正な型

        # 1. ユーザーIDが管理者リストに含まれるか
        if member.id in self.admin_user_ids:
            return True
            
        # 2. サーバーのメンバーでない場合 (DMなど) はロールチェック不可
        if not isinstance(member, discord.Member):
            return False

        # 3. サーバーの所有者か (所有者は常に管理者とみなす)
        if member.guild.owner_id == member.id:
            return True
            
        # 4. ユーザーが持つロール名が管理者ロールリストに含まれるか
        try:
            member_role_names = {role.name for role in member.roles}
            if not member_role_names.isdisjoint(self.admin_role_names):
                # (isdisjoint: 積集合が空の場合 True。not で積集合があるか(共通項があるか)を判定)
                return True
        except Exception as e:
            self.logger.warning(f"is_bot_admin でのロール名比較中にエラー: {e}")

        return False

    def _check_specific_permission(
        self, 
        member: discord.Member | discord.User, 
        feature_name: str
    ) -> bool:
        """
        (内部) [Command_xxx] や [Feature_xxx] の特定権限をチェックするロジック
        """
        section_name_cmd = f"Command_{feature_name}"
        section_name_feat = f"Feature_{feature_name}"
        
        section_name = None
        if section_name_cmd in self.config:
            section_name = section_name_cmd
        elif section_name_feat in self.config:
            section_name = section_name_feat
        else:
            # この機能固有の設定がiniに存在しない
            self.logger.debug(f"権限チェック: '{feature_name}' の固有設定は config.ini にありません。")
            return False # 固有設定がない場合は False (Adminのみ許可へ)

        # 固有設定を読み込む
        allowed_roles_str = self.config.get(section_name, 'AllowedRoles', fallback='')
        allowed_users_str = self.config.get(section_name, 'AllowedUsers', fallback='')
        
        allowed_role_names = _parse_list_from_ini(allowed_roles_str)
        allowed_user_ids = [int(uid) for uid in _parse_list_from_ini(allowed_users_str) if uid.isdigit()]

        # 1. ユーザーIDが許可リストに含まれるか
        if member.id in allowed_user_ids:
            return True
            
        # 2. ロールチェック (Memberである必要あり)
        if isinstance(member, discord.Member):
            member_role_names = {role.name for role in member.roles}
            if not member_role_names.isdisjoint(allowed_role_names):
                return True
                
        return False

    # --- discord.py の check デコレータ ---

    def admin_only(self):
        """
        機能名: (デコレータ) ボット管理者のみ
        説明:
            コマンドの実行者を is_bot_admin() でチェックするデコレータ。
            権限がない場合は commands.CheckFailure を発生させます。
        引数: なし
        戻り値:
            Callable: discord.py の check デコレータ
        """
        
        async def predicate(ctx_or_interaction: commands.Context | discord.Interaction) -> bool:
            member = self._get_ctx_or_interaction_member(ctx_or_interaction)
            if not member:
                return False # ユーザーが取得できない
            
            if self.is_bot_admin(member):
                return True
            
            # ErrorHandler がこれを検知する
            raise commands.CheckFailure("このコマンドはボット管理者のみ実行できます。")

        # プレフィックスコマンドとスラッシュコマンドの両方に対応させる
        return commands.check(predicate)

    def requires_permission(self, feature_name: str):
        """
        機能名: (デコレータ) 特定権限
        説明:
            config.ini の [Command_feature_name] または [Feature_feature_name]
            セクションに基づき、特定の権限をチェックするデコレータ。
            ボット管理者は常に許可されます。
        引数:
            feature_name (str): config.ini で定義したコマンド/機能名
        戻り値:
            Callable: discord.py の check デコレータ
        """
        
        async def predicate(ctx_or_interaction: commands.Context | discord.Interaction) -> bool:
            member = self._get_ctx_or_interaction_member(ctx_or_interaction)
            if not member:
                return False

            # 1. ボット管理者は常に許可
            if self.is_bot_admin(member):
                return True
                
            # 2. 固有の権限設定をチェック
            if self._check_specific_permission(member, feature_name):
                return True

            raise commands.CheckFailure(f"このコマンドの実行には '{feature_name}' の権限が必要です。")

        return commands.check(predicate)


# --- 利用例 (このファイルが直接実行された場合) ---

# テストのために、discord のダミー（モック）を作成します
class MockRole:
    def __init__(self, name: str):
        self.name = name
    def __repr__(self):
        return f"<MockRole(name='{self.name}')>"

class MockGuild:
    def __init__(self, owner_id: int):
        self.owner_id = owner_id
    def __repr__(self):
        return f"<MockGuild(owner_id={self.owner_id})>"

class MockUser:
    def __init__(self, id: int, name: str = "MockUser"):
        self.id = id
        self.name = name
    def __repr__(self):
        return f"<MockUser(id={self.id})>"

class MockMember(MockUser):
    def __init__(self, id: int, name: str, roles: list[MockRole], guild: MockGuild):
        super().__init__(id, name)
        self.roles = roles
        self.guild = guild
    def __repr__(self):
        return f"<MockMember(id={self.id}, roles={self.roles})>"

if __name__ == "__main__":
    import os

    print("--- PermissionManager 単体テスト ---")

    # 1. 依存関係のセットアップ (Config, Logger)
    try:
        # 1階層上のディレクトリにあると仮定
        ini_path = '../config.ini'
        env_path = '../.env'
        
        # テスト用のダミー config.ini を作成
        # (実際の config.ini が存在しない場合でもテストできるように)
        if not os.path.exists(ini_path):
            ini_path = 'temp_test_config.ini'
            with open(ini_path, 'w', encoding='utf-8') as f:
                f.write("""
[Logging]
LogLevel = DEBUG
NotifyErrorToDiscord = False

[Permissions]
AdminRoles = BotAdmin, 運営
AdminUsers = 100001, 100002

[Command_hr_tool]
AllowedRoles = 人事部
AllowedUsers = 200001
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
        
    except Exception as e:
        print(f"ConfigManager/Loggerの初期化に失敗: {e}")
        exit(1)

    # 2. PermissionManager の初期化
    pm = PermissionManager(config_manager, logger)
    logger.info("--- PermissionManager 初期化完了 ---")
    
    # 3. テスト用のエンティティ作成
    guild = MockGuild(owner_id=500000)
    
    # (a) 管理者 (ID指定)
    admin_by_id = MockMember(100001, "AdminByID", [], guild)
    
    # (b) 管理者 (ロール指定)
    admin_by_role = MockMember(100003, "AdminByRole", [MockRole("運営")], guild)
    
    # (c) 人事部 (特定権限)
    hr_by_role = MockMember(200002, "HRByRole", [MockRole("人事部")], guild)
    
    # (d) 人事部 (ID指定)
    hr_by_id = MockMember(200001, "HRByID", [], guild)
    
    # (e) 一般ユーザー
    normal_user = MockMember(300001, "NormalUser", [MockRole("一般")], guild)
    
    # (f) サーバーオーナー
    guild_owner = MockMember(guild.owner_id, "GuildOwner", [], guild)

    # 4. is_bot_admin() のテスト
    logger.info("--- [Test 1] is_bot_admin() ---")
    assert pm.is_bot_admin(admin_by_id) == True, "管理者(ID)の判定失敗"
    logger.info("OK: 管理者(ID)")
    assert pm.is_bot_admin(admin_by_role) == True, "管理者(ロール)の判定失敗"
    logger.info("OK: 管理者(ロール)")
    assert pm.is_bot_admin(guild_owner) == True, "管理者(オーナー)の判定失敗"
    logger.info("OK: 管理者(オーナー)")
    assert pm.is_bot_admin(hr_by_role) == False, "人事部ユーザーが管理者と誤判定"
    logger.info("OK: 人事部ユーザー (非管理者)")
    assert pm.is_bot_admin(normal_user) == False, "一般ユーザーが管理者と誤判定"
    logger.info("OK: 一般ユーザー (非管理者)")

    # 5. _check_specific_permission() のテスト (内部関数)
    logger.info("--- [Test 2] _check_specific_permission('hr_tool') ---")
    assert pm._check_specific_permission(hr_by_role, 'hr_tool') == True, "特定権限(ロール)の判定失敗"
    logger.info("OK: 特定権限(ロール)")
    assert pm._check_specific_permission(hr_by_id, 'hr_tool') == True, "特定権idem(ID)の判定失敗"
    logger.info("OK: 特定権限(ID)")
    assert pm._check_specific_permission(normal_user, 'hr_tool') == False, "一般ユーザーが特定権限と誤判定"
    logger.info("OK: 一般ユーザー (権限なし)")
    assert pm._check_specific_permission(admin_by_id, 'hr_tool') == False, "管理者(ID)が特定権限(ロール)と誤判定"
    logger.info("OK: 管理者 (固有権限は持たない)")

    logger.info("--- PermissionManager テスト完了 ---")

    # テスト用に作成したファイルを削除
    if 'temp_test' in ini_path:
        os.remove(ini_path)
        print(f"'{ini_path}' を削除しました。")
    if 'temp_test' in env_path:
        os.remove(env_path)
        print(f"'{env_path}' を削除しました。")