import discord
from discord.ext import commands
from discord import app_commands

#  utils から権限マネージャーをインポート
# (main.py で bot に登録されたものを利用するため、型ヒントとして使う)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from utils.permission_handler import PermissionManager
    from utils.db_manager import GuildSettingRepository

class ExampleCog(commands.Cog):
    """
    機能追加(Cog)のサンプルクラス
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # main.py で bot にアタッチされたユーティリティを取得
        self.logger = bot.logger
        self.permission_manager: 'PermissionManager' = bot.permission_manager
        self.guild_repo: 'GuildSettingRepository' = bot.guild_repo

    # ----------------------------------------------------
    # 1. スラッシュコマンド (推奨)
    # ----------------------------------------------------

    @app_commands.command(name="greet", description="挨拶をします。")
    async def slash_greet(self, interaction: discord.Interaction):
        """スラッシュコマンドの例"""
        await interaction.response.send_message(f"こんにちは、{interaction.user.mention}さん！")
        self.logger.info(f"[ExampleCog] {interaction.user} が /greet を実行しました。")

    @app_commands.command(name="admin_test", description="ボット管理者専用コマンドのテスト")
    @app_commands.check(lambda i: i.client.permission_manager.is_bot_admin(i.user)) # 👈 権限チェック
    async def slash_admin_test(self, interaction: discord.Interaction):
        """権限チェック (Admin Only) の例"""
        await interaction.response.send_message("あなたはボット管理者です。", ephemeral=True)

    @app_commands.command(name="hr_command", description="人事部専用コマンドのテスト")
    @app_commands.check(lambda i: i.client.permission_manager._check_specific_permission(i.user, 'hr_tool')) # 👈 権限チェック
    async def slash_hr_test(self, interaction: discord.Interaction):
        """権限チェック (hr_tool) の例"""
        await interaction.response.send_message("あなたは人事部権限を持っています。", ephemeral=True)

    # ----------------------------------------------------
    # 2. プレフィックスコマンド (従来型)
    # ----------------------------------------------------
    
    @commands.command(name="ping")
    async def prefix_ping(self, ctx: commands.Context):
        """プレフィックスコマンドの例"""
        await ctx.reply(f"Pong! ({round(self.bot.latency * 1000)}ms)")

    @commands.command(name="admin_only")
    @commands.check(lambda ctx: ctx.bot.permission_manager.is_bot_admin(ctx.author)) # 👈 権限チェック
    async def prefix_admin_only(self, ctx: commands.Context):
        """権限チェック (Admin Only) の例"""
        await ctx.reply("あなたはボット管理者です。")

# BotにCogを登録するための必須関数
async def setup(bot: commands.Bot):
    await bot.add_cog(ExampleCog(bot))
    bot.logger.info("[ExampleCog] がロードされました。")