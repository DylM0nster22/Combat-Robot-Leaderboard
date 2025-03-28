# Combat Robot Discord Bot

A Discord bot that scrapes [robotcombatevents.com](https://robotcombatevents.com) pages like  
`https://www.robotcombatevents.com/groups/3946/resources/19655`  
to track a bot’s rank, weight class, and total event points.  
Data is stored in a **Supabase** Postgres table and displayed via slash commands.

---

## 1. Prerequisites

1. **Discord Developer Portal**  
   - Create a new Application + Bot  
   - Turn on “MESSAGE CONTENT INTENT” and “SERVER MEMBERS INTENT” in the bot settings  
   - Copy your **Bot Token**

2. **Supabase** (Free)  
   - Sign up at [supabase.com](https://supabase.com)  
   - Create a project (free tier)  
   - Copy your **Project URL** and **anon key** from **Project Settings > API**  
   - In the Table Editor, create a table **`tracked_bots`** with columns:
     - `id` (uuid, default `gen_random_uuid()`, primary key)
     - `bot_name` (text)
     - `bot_url` (text)
     - `rank` (integer)
     - `weight_class` (text)
     - `total_points` (numeric or float)

3. **Render**  
   - Sign up at [render.com](https://render.com)  
   - Connect your GitHub repo

---

## 2. Local Testing (Optional)

1. Clone this repo locally.  
2. Install dependencies:  
   ```bash
   pip install -r requirements.txt
