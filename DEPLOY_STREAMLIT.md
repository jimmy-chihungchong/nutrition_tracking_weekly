# Deploy to Streamlit Community Cloud

This puts the tracker online at a `https://<your-app>.streamlit.app` address, for free. You need a **GitHub
account** and, optionally, an **NVIDIA NIM API key** from [build.nvidia.com](https://build.nvidia.com) for AI
estimates. Without the key the app runs in demo mode.

> **Storage is temporary in this setup.** The app saves meals to an Excel file on Streamlit's server, and that
> file is **reset whenever the app restarts, goes to sleep after inactivity, or is updated**. Each fresh start
> comes with sample meals. Goal changes made on the Goals page are also reset. Everyone who opens the app shares
> the same data. To keep a copy, use **⬇ Download meal log (Excel)** in the sidebar.
> For permanent storage, see [Optional: keep data permanently](#optional-keep-data-permanently-google-sheets).

---

## 1. Put the code on GitHub

Create a repository on [github.com/new](https://github.com/new). **Private is free** and recommended; public
also works (your keys are never in the code). Don't add a README. Then in this project folder run:

```bash
git init
git add .
git status
```

Check the list: it must **not** include `.env`, `.streamlit/secrets.toml`, `.venv/` or `data/nutrition_log.xlsx`
(all are in `.gitignore`, so they shouldn't appear). Then:

```bash
git commit -m "Weekly Nutrition Tracker"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git
git push -u origin main
```

## 2. Deploy on Streamlit

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub (allow access to the repo).
2. **Create app → Deploy a public app from GitHub**: pick your repo, branch `main`, main file `streamlit_app.py`.
3. **Advanced settings**: Python **3.12**. Under **Secrets**, paste (edit to suit, or leave empty for demo mode):

   ```toml
   APP_TIMEZONE = "Asia/Kolkata"
   LLM_API_KEY = "nvapi-your-key-here"
   ```

   `APP_TIMEZONE` makes "today" match your day; the server runs on UTC.
4. **Deploy**. The first start takes a few minutes while packages install. The sidebar shows the nutrition
   engine and a **Temporary storage** notice.

## 3. Decide who can open it

There's no login, so anyone who can open the app can log, edit and delete meals, and use your AI key. Check the
app's **Settings → Sharing** to see who can view it. If it's public, only share the link with people you trust,
or leave out `LLM_API_KEY` so nobody can use your key.

## After deploying

- **Updates:** push to GitHub and the app redeploys automatically. This also resets the stored meals, so
  download a backup first if you need it. Secrets stay in Streamlit.
- **Sleeping:** free apps sleep after a period without visitors; the next visitor wakes them up (about 30 s),
  with the meal log reset to the sample data.
- **Goals:** changes on the Goals page last until the next restart. To change the defaults permanently, edit
  `data/weekly_targets.csv` and push it to GitHub.
- **Local use is unchanged:** on your own computer, `streamlit run streamlit_app.py` or `python app.py` keep
  your meals in `data/nutrition_log.xlsx` permanently.

## Troubleshooting

| What you see | Fix |
|---|---|
| "Demo mode" in the sidebar | No `LLM_API_KEY` in secrets (optional). |
| Dates are a day off | Set `APP_TIMEZONE` in secrets. |
| My meals disappeared | The app restarted or slept, which resets temporary storage (see the note at the top). |

---

## Optional: keep data permanently (Google Sheets)

The app can store meals and goals in a Google Sheet instead. This uses a Google Cloud *service account*. As far as
we know the Google Sheets API is free and doesn't need a billing account, but Google may ask for card details
when you first sign up for Google Cloud. You don't need any code changes; just add secrets:

1. Create an empty Google Sheet and copy its URL. Under **Share → General access**, keep it **Restricted**.
2. In [console.cloud.google.com](https://console.cloud.google.com): create a project → **APIs & Services →
   Library → Google Sheets API → Enable** → **IAM & Admin → Service Accounts → Create** → open it →
   **Keys → Add key → JSON** (keep this file private and out of the project folder).
3. Share the sheet with the service account's email as **Editor**.
4. Add to your Streamlit secrets the `[google_sheets]` and `[gcp_service_account]` sections from
   `.streamlit/secrets.toml.example`, filled in from the JSON file (keep `private_key` exactly as in the JSON,
   including the `\n` sequences).

After a reboot the sidebar says **💾 Data: Google Sheets**, and the sheet gets **Meals**, **Column Keys** and
**Targets** tabs.

| Message | Fix |
|---|---|
| "Google Sheets couldn't … (403 …)" | The sheet isn't shared with the service account email, or the Sheets API isn't enabled. |
| "… (404 …)" / "SpreadsheetNotFound" | `spreadsheet_url` is wrong. Copy it again from the browser. |
| Sidebar still says "local Excel file" | Secrets must contain both `[google_sheets]` and `[gcp_service_account]`. |
