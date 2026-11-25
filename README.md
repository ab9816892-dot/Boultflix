<p align="center">
  <h1 align="center">⚡ Boultflix Auto Filter Bot</h1>
  <p align="center">Ultra-Fast, Scalable Telegram Media Indexing & Update Notification Bot</p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Pyrogram-v2-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Pyrogram">
  <img src="https://img.shields.io/badge/MongoDB-Atlas-47A248?style=for-the-badge&logo=mongodb&logoColor=white" alt="MongoDB">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License">
</p>

---

## 📌 Features

- ⚡ **Ultra-Fast Media Queue**: Non-blocking asynchronous queue processing that easily ingests bursts of 100-200+ media files with zero dropped updates.
- 🎬 **Channel Movie Updates**: Automatic metadata fetching via TMDb API with smart debounced season & resolution merging.
- 🧠 **Low Memory Footprint**: Engineered to run smoothly on constrained 512MB RAM free instances (e.g. Render) without OOM crashes.
- 🔍 **Intelligent Search**: Lightning-fast search results, fuzzy spell check, pagination, quality & language filters.
- 🛡️ **Anti-Flood & Auto-Reconnect**: Resilient Telegram FloodWait handling and automatic database heartbeat.
- 💳 **Premium & Subscription Management**: Flexible plans, stars integration, and verification shortener support.

---

## 🚀 Setup & Deployment

### 1. Environment Variables
Create a `.env` file or configure your cloud provider's environment variables:

| Variable | Description |
| :--- | :--- |
| `API_ID` | Telegram API ID from my.telegram.org |
| `API_HASH` | Telegram API Hash from my.telegram.org |
| `BOT_TOKEN` | Bot token from @BotFather |
| `DATABASE_URI` | MongoDB Connection URI |
| `DATABASE_NAME` | MongoDB database name |
| `ADMINS` | Space-separated Telegram Admin user IDs |
| `CHANNELS` | Space-separated channel IDs to index files from |
| `MOVIE_UPDATE_CHANNEL` | Channel ID for movie update notifications |
| `LOG_CHANNEL` | Channel ID for logging events |
| `TMDB_API_KEY` | TMDB API Key for movie metadata |

### 2. Run with Docker / Local
```bash
# Clone repository
git clone https://github.com/ab9816892-dot/Boultflix.git
cd Boultflix

# Install dependencies
pip install -r requirements.txt

# Start Bot
python bot.py
```

---

## 📄 License
MIT License.
