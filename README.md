# Word Chain Telegram Bot

A game bot for Telegram with virtual coins and challenges.

## Features
- Word chain gameplay
- Virtual currency system
- Player challenges
- Leaderboards

## Deployment

### 1. Render Setup
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

1. Click the deploy button
2. Set environment variables:
   - `BOT_TOKEN`: Your Telegram bot token
   - `DATABASE_URL`: From Render PostgreSQL dashboard
3. Add `words.txt` with your word list

### 2. Local Development
```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your values
python bot.py
