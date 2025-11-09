<p align="center">
  <img src="https://dummyimage.com/1200x200/0d0d0d/ffffff&text=⚙️+Roblox+Username+Checker" alt="Roblox Username Checker Banner">
</p>

---

# ⚙️ Roblox Username Checker

Fast, async Roblox username checker — built with **Python + httpx + asyncio**.  
Features **adaptive rate limiting**, **auto-retry**, **CSRF handling**, and **instant browser/beep alerts** when an available username is found. 🚀

---

## 🧩 Requirements

Install dependencies:
```bash
pip install -r requirements.txt
Your requirements.txt should contain:

httpx>=0.24.0

▶️ How to Run

Place your list of usernames in a file named usernames.txt, for example:

name1
name2
name3


Open a terminal in the project folder and run:

python main.py


The script will:

Load all usernames from usernames.txt

Check them using Roblox’s username validation API

Stop automatically when it finds one that’s available

When a username is found, it will:

🔔 Beep 3 times

🌐 Open the Roblox website automatically

🧠 How It Works

Uses Roblox’s public endpoint:

https://auth.roblox.com/v1/usernames/validate


Async requests handled by httpx.AsyncClient

Smart adaptive rate limiting:

Slows down on 429 Too Many Requests

Speeds up when responses are stable

Automatically retries on network errors or token issues

Logs all responses to responses.log

🗂️ Output Files
File	Description
taken.txt	Usernames that are unavailable
available.txt	First available username found
responses.log	Full API responses and logs
🧾 Notes

This was a quick script built with AI assistance.
All main features should work as intended — but feel free to improve or fork it. 💡

💬 Author

Created with ❤️ using Python and curiosity.
