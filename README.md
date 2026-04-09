# Telegram Planner Bot

Telegram planner bot for Russian-speaking users with task planning, reminders, timers, Pinterest-style memes, and motivational phrases directly inside Telegram.

## Demo

- Main Telegram bot chat with button-based navigation
- Task creation flow with inline calendar and priorities
- Productivity chart with daily and all-time progress
- Hourly reminders with interactive task actions

## Product Context

### End Users

- Students
- Busy professionals
- Anyone who plans daily tasks inside Telegram

### Problem That The Product Solves

- People forget tasks and deadlines
- It is inconvenient to track priorities and progress in plain notes
- Users need a lightweight planner without opening a separate app

### Our Solution

- A Telegram bot with date-based planning
- Inline calendar and priority selection
- Hourly reminders for today’s unfinished tasks
- Timers, productivity statistics, task deletion, and progress reset
- Meme reactions inspired by Pinterest images and short motivational phrases for engagement

## Features

### Implemented

- Add task with step-by-step flow: text -> date -> priority
- Inline calendar for date selection
- Priority labels for urgent, medium, and low-priority tasks
- Views for today, tomorrow, week, and selected calendar date
- Mark task as done or undo completion
- Postpone task to tomorrow
- Delete a single task
- Reset all tasks and statistics from the bot
- Hourly reminders for today’s unfinished tasks
- Per-user media on/off toggle
- Task timers with start and pause
- Productivity pie chart for all time and current day

### Not Yet Implemented

- Multi-user collaboration on shared task boards
- Cloud database sync
- Web admin dashboard
- Export to calendar services

## Usage

1. Start the bot with `/start`
2. Use the buttons or `/add` to create a task
3. Pick a date in the inline calendar
4. Choose a priority
5. Open today, tomorrow, week, or calendar views
6. Mark tasks done, postpone, delete, or run timers
7. Use the productivity view to track progress
8. Use `репродуктивация` to clear all tasks and statistics

## Deployment

### OS

- Ubuntu 24.04

### What Should Be Installed On The VM

- Python 3.11+
- `python3-venv`
- `pip`
- `systemd`

### Step-By-Step Deployment Instructions

1. Clone the repository
2. Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Create `.env` from `.env.example` and set the Telegram token:

```bash
cp .env.example .env
```

5. Run the bot manually:

```bash
python run.py
```

6. Create a `systemd` service:

```ini
[Unit]
Description=Telegram Planner Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/tg_planner_bot
ExecStart=/opt/tg_planner_bot/.venv/bin/python run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

7. Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable tg-planner-bot
sudo systemctl start tg-planner-bot
```
