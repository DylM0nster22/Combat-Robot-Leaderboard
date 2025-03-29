import os
import re
import discord
import aiohttp
import asyncio
from discord.ext import commands, tasks
from bs4 import BeautifulSoup
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

# ---- Environment Setup ----
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL") or ""
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or ""

# Allowed channels for commands (set these in your .env as comma-separated IDs)
ALLOWED_CHANNEL_IDS = []
if os.getenv("ALLOWED_CHANNEL_IDS"):
    ALLOWED_CHANNEL_IDS = [int(ch.strip()) for ch in os.getenv("ALLOWED_CHANNEL_IDS").split(",")]

# Dedicated channel for the auto-updating leaderboards
LEADERBOARD_CHANNEL_ID = None
if os.getenv("LEADERBOARD_CHANNEL_ID"):
    LEADERBOARD_CHANNEL_ID = int(os.getenv("LEADERBOARD_CHANNEL_ID"))

# ---- Supabase Setup ----
from supabase import create_client, Client

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    print("Warning: Supabase URL/Key not set. DB commands will not work if used.")

# ---- Discord Setup ----
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True

bot = commands.Bot(command_prefix="!", intents=INTENTS)
tree = bot.tree

# ---- ADMIN IDs (for commands restricted to specific users) ----
ADMIN_IDS = [
    # e.g., "123456789012345678"
]

# Global dictionary to store dedicated leaderboard messages by weight class
leaderboard_messages = {}

# ---------------------------
# HELPER: Simplify Weight Class
# ---------------------------
def simplify_weight_class(weight_class: str) -> str:
    """
    If the weight class string contains a dash (e.g., "1lb - Plastic Antweight"),
    use the part after the dash. Otherwise, return the trimmed string.
    """
    if '-' in weight_class:
        parts = weight_class.split('-')
        return parts[1].strip() if len(parts) > 1 else parts[0].strip()
    return weight_class.strip()

# ---------------------------
# ASYNCHRONOUS SCRAPER
# ---------------------------
async def scrape_bot_page(bot_url: str) -> dict:
    """
    Asynchronously scrapes a bot page from robotcombatevents.com and returns a dict with:
      - bot_name
      - weight_class
      - rank
      - total_points
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(bot_url, timeout=10) as response:
                text = await response.text()
    except Exception as e:
        print(f"[ERROR] Could not fetch {bot_url}: {e}")
        return {}

    soup = BeautifulSoup(text, "html.parser")

    # ---- Parse RANK ----
    rank_div = soup.select_one("div.resource-header-rank-container.box")
    rank_value = 9999
    if rank_div:
        text_rank = rank_div.get_text(separator=" ", strip=True)
        match = re.search(r"\d+", text_rank)
        if match:
            rank_value = int(match.group())

    # ---- Parse BOT NAME and WEIGHT CLASS ----
    title_container = soup.select_one("div.resource-header-title-container")
    bot_name = "Unknown Bot"
    weight_class = "Unknown Weight"
    if title_container:
        name_div = title_container.select_one("div.resource-header-title")
        if name_div:
            bot_name = name_div.get_text(strip=True)
        subtitle_div = title_container.select_one("div.resource-header-subtitle")
        if subtitle_div:
            weight_class = subtitle_div.get_text(strip=True)

    # ---- Parse HISTORY TABLE for total points ----
    history_container = soup.select_one("div.resource-history-body-table")
    total_points = 0.0
    if history_container:
        table = history_container.find("table")
        if table:
            tbody = table.find("tbody")
            if tbody:
                rows = tbody.find_all("tr")
                for row in rows:
                    cols = row.find_all("td")
                    if len(cols) >= 3:
                        points_str = cols[2].get_text(strip=True)
                        try:
                            total_points += float(points_str)
                        except ValueError:
                            pass

    return {
        "bot_name": bot_name,
        "weight_class": weight_class,
        "rank": rank_value,
        "total_points": total_points
    }

# ---------------------------
# SUPABASE HELPER FUNCTIONS
# ---------------------------
async def add_or_update_bot(bot_url: str) -> str:
    if not supabase:
        return "[ERROR] Supabase not configured."

    data = await scrape_bot_page(bot_url)
    if not data:
        return f"[ERROR] Failed to parse the bot page: {bot_url}"

    bot_name = data["bot_name"]
    weight_class = data["weight_class"]
    rank_value = data["rank"]
    total_points = data["total_points"]

    # Check if this bot_url is already in DB
    existing = await asyncio.to_thread(lambda: supabase.table("tracked_bots").select("*").eq("bot_url", bot_url).execute())
    if existing.data:
        row_id = existing.data[0]["id"]
        await asyncio.to_thread(lambda: supabase.table("tracked_bots").update({
            "bot_name": bot_name,
            "weight_class": weight_class,
            "rank": rank_value,
            "total_points": total_points
        }).eq("id", row_id).execute())
        return f"Updated existing bot: {bot_name} (Rank {rank_value}, {total_points} pts)."
    else:
        await asyncio.to_thread(lambda: supabase.table("tracked_bots").insert({
            "bot_name": bot_name,
            "bot_url": bot_url,
            "weight_class": weight_class,
            "rank": rank_value,
            "total_points": total_points
        }).execute())
        return f"Added new bot: {bot_name} (Rank {rank_value}, {total_points} pts)."

async def remove_bot(bot_url: str) -> str:
    if not supabase:
        return "[ERROR] Supabase not configured."

    existing = await asyncio.to_thread(lambda: supabase.table("tracked_bots").select("*").eq("bot_url", bot_url).execute())
    if not existing.data:
        return f"No bot found for URL {bot_url}"

    row_id = existing.data[0]["id"]
    await asyncio.to_thread(lambda: supabase.table("tracked_bots").delete().eq("id", row_id).execute())
    return f"Removed bot with URL {bot_url}."

async def get_all_bots():
    if not supabase:
        return []
    res = await asyncio.to_thread(lambda: supabase.table("tracked_bots").select("*").execute())
    return res.data if res.data else []

async def refresh_all_bots() -> str:
    if not supabase:
        return "[ERROR] Supabase not configured."

    bots = await get_all_bots()
    for b in bots:
        url = b["bot_url"]
        data = await scrape_bot_page(url)
        if data:
            await asyncio.to_thread(lambda: supabase.table("tracked_bots").update({
                "bot_name": data["bot_name"],
                "weight_class": data["weight_class"],
                "rank": data["rank"],
                "total_points": data["total_points"]
            }).eq("id", b["id"]).execute())
    return "Refreshed all tracked bots."

# ---------------------------
# HELPER: Check if command is in allowed channel
# ---------------------------
def is_channel_allowed(interaction: discord.Interaction) -> bool:
    if ALLOWED_CHANNEL_IDS and interaction.channel:
        return interaction.channel.id in ALLOWED_CHANNEL_IDS
    return True  # if not set, allow in any channel

# ---------------------------
# DISCORD SLASH COMMANDS
# ---------------------------
@tree.command(name="addbot", description="Add or update a combat bot by URL")
async def addbot_command(interaction: discord.Interaction, bot_url: str):
    if not is_channel_allowed(interaction):
        await interaction.response.send_message("This command can only be used in designated channels.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    if ADMIN_IDS and str(interaction.user.id) not in ADMIN_IDS:
        await interaction.followup.send("You are not authorized to add bots.")
        return

    result = await add_or_update_bot(bot_url)
    await interaction.followup.send(result)
    
    # Update the dedicated leaderboard immediately after adding/updating a bot.
    # You can either await the update or schedule it as a background task.
    # Here we use create_task so that it doesn't block the command response.
    bot.loop.create_task(update_leaderboard_messages())

@tree.command(name="removebot", description="Remove a bot from the leaderboard by URL")
async def removebot_command(interaction: discord.Interaction, bot_url: str):
    if not is_channel_allowed(interaction):
        await interaction.response.send_message("This command can only be used in designated channels.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    if ADMIN_IDS and str(interaction.user.id) not in ADMIN_IDS:
        await interaction.followup.send("You are not authorized to remove bots.")
        return

    result = await remove_bot(bot_url)
    await interaction.followup.send(result)

@tree.command(name="leaderboard", description="Show the combat robot leaderboards by weight class")
async def leaderboard_command(interaction: discord.Interaction):
    if not is_channel_allowed(interaction):
        await interaction.response.send_message("This command can only be used in designated channels.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    bots = await get_all_bots()
    if not bots:
        await interaction.followup.send("No bots tracked yet.")
        return

    # Group bots by simplified weight class
    groups = {}
    for b in bots:
        wc = b.get("weight_class", "Unknown Weight")
        simple_wc = simplify_weight_class(wc)
        groups.setdefault(simple_wc, []).append(b)
    
    response_text = ""
    for weight, group in groups.items():
        group_sorted = sorted(group, key=lambda b: (-b["total_points"], b["rank"]))
        response_text += f"**Leaderboard - {weight}:**\n"
        for i, b in enumerate(group_sorted, start=1):
            response_text += f"{i}. {b['bot_name']} (Rank {b['rank']}, {b['total_points']} pts, {b['weight_class']})\n"
        response_text += "\n"
    await interaction.followup.send(response_text)

@tree.command(name="refresh", description="Refresh all tracked bots from the site (admin only)")
async def refresh_command(interaction: discord.Interaction):
    if not is_channel_allowed(interaction):
        await interaction.response.send_message("This command can only be used in designated channels.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    if ADMIN_IDS and str(interaction.user.id) not in ADMIN_IDS:
        await interaction.followup.send("You are not authorized to refresh data.")
        return

    result = await refresh_all_bots()
    await interaction.followup.send(result)

leaderboard_update_lock = asyncio.Lock()

async def update_leaderboard_messages():
    """
    Deletes all messages in the dedicated leaderboard channel, then re-posts 
    the current leaderboards grouped by weight class.
    """
    # If an update is already running, skip this call.
    if leaderboard_update_lock.locked():
        print("Leaderboard update already in progress; skipping duplicate call.")
        return

    async with leaderboard_update_lock:
        if LEADERBOARD_CHANNEL_ID is None:
            print("No dedicated leaderboard channel ID provided.")
            return

        channel = bot.get_channel(LEADERBOARD_CHANNEL_ID)
        if not channel:
            print("Leaderboard channel not found.")
            return

        # 1) Purge all messages in the channel (this will delete messages younger than 14 days)
        try:
            await channel.purge()
        except Exception as e:
            print(f"Error purging channel: {e}")
            return

        # 2) Fetch the current bots from Supabase
        bots = await get_all_bots()
        if not bots:
            try:
                msg = await channel.send("No bots tracked yet.")
            except Exception as e:
                print(f"Error sending 'No bots tracked yet' message: {e}")
            return

        # 3) Group bots by simplified weight class
        groups = {}
        for b in bots:
            wc = b.get("weight_class", "Unknown Weight")
            simple_wc = simplify_weight_class(wc)
            groups.setdefault(simple_wc, []).append(b)

        # 4) Create a leaderboard message for each weight class
        for weight_class, bot_list in groups.items():
            # Sort: highest total_points first, then by rank ascending
            bot_list_sorted = sorted(
                bot_list,
                key=lambda b: (-b["total_points"], b["rank"])
            )

            # Build text for the embed
            leaderboard_text = "\n".join(
                f"**{i}. {b['bot_name']}** (Rank {b['rank']}, {b['total_points']} pts, {b['weight_class']})"
                for i, b in enumerate(bot_list_sorted, start=1)
            )

            embed = discord.Embed(
                title=f"Combat Robot Leaderboard - {weight_class}",
                description=leaderboard_text,
                color=discord.Color.blue()
            )

            try:
                await channel.send(embed=embed)
            except Exception as e:
                print(f"Error sending leaderboard for {weight_class}: {e}")


@tasks.loop(hours=24)
async def leaderboard_updater():
    await update_leaderboard_messages()

@leaderboard_updater.before_loop
async def before_leaderboard_updater():
    await bot.wait_until_ready()

# ---------------------------
# BOT EVENTS
# ---------------------------
@bot.event
async def on_ready():
    await tree.sync()
    print(f"Bot is online as {bot.user} (ID: {bot.user.id}).")
    # Start the background leaderboard updater.
    leaderboard_updater.start()
    # Update leaderboards immediately on startup.
    await update_leaderboard_messages()

def main():
    if not DISCORD_TOKEN:
        print("[ERROR] DISCORD_TOKEN not set. Exiting.")
        return
    bot.run(DISCORD_TOKEN)

from threading import Thread
from aiohttp import web

async def handle(request):
    return web.Response(text="Bot is running!")

def run_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    web.run_app(app, port=8080)

Thread(target=run_web_server).start()

if __name__ == "__main__":
    main()
