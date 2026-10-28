# Putting ExamBuilder online (free, on PythonAnywhere)

PythonAnywhere's free plan gives you a permanent disk (so your database and
PDFs survive restarts), HTTPS, and an address like
`https://yourname.pythonanywhere.com`. It never sleeps.

**Limits to know about**
- 512 MB of disk. ExamBuilder uses about 170 MB (packages 82 MB, PDFs 80 MB, database 5 MB).
- **Once a month you must log in and press "Run until 1 month from today"** on the Web tab,
  or the site switches off. PythonAnywhere emails you a reminder.
- The server can only reach approved websites. Resend (for the interest-form emails) is approved.

Everything that downloads, splits and tags papers stays on your Mac. The server just runs the website.

---

## 1. Make the upload file (on your Mac)

```
cd ~/Documents/ExamBuilder
.venv/bin/python -m scripts.make_deploy_zip
```

This creates `deploy/ExamBuilder.zip` (about 76 MB): the code, the PDFs and your database.
It never contains your `.env` secrets.

## 2. Create the account

1. Go to <https://www.pythonanywhere.com> → **Pricing & signup** → **Create a Beginner account** (free).
2. Your username becomes your web address (`username.pythonanywhere.com`), so choose it carefully.

## 3. Upload and unzip

1. Open the **Files** tab → **Upload a file** → choose `deploy/ExamBuilder.zip`.
2. Open the **Consoles** tab → **Bash**. In the console:

```
unzip -q ExamBuilder.zip
cd ExamBuilder
```

## 4. Install the packages

Still in the Bash console (this takes a few minutes):

```
mkvirtualenv --python=python3.13 exambuilder
pip install -r requirements-server.txt
```

If `python3.13` isn't available, use the newest Python 3 that is (3.11 or later), e.g. `python3.12`.

## 5. Create your secrets file on the server

```
python scripts/setup_env.py
```

- Choose your username and password (they can differ from your Mac's).
- Paste your Resend API key and your email address if you want interest-form emails.
- Answer **y** to "Is this the live website on PythonAnywhere?"

## 6. Create the web app

1. Open the **Web** tab → **Add a new web app** → **Next**.
2. Choose **Manual configuration** (not "Flask") → the **same Python version** as step 4 → **Next**.
3. On the Web tab, set:
   - **Virtualenv:** `/home/USERNAME/.virtualenvs/exambuilder`
   - **Static files:** URL `/static/` → Directory `/home/USERNAME/ExamBuilder/app/static`
   - **Force HTTPS:** on
4. Click the **WSGI configuration file** link (`/var/www/username_pythonanywhere_com_wsgi.py`),
   delete everything in it, and replace it with (change USERNAME):

```python
import sys

project = "/home/USERNAME/ExamBuilder"
if project not in sys.path:
    sys.path.insert(0, project)

from wsgi import app as application  # noqa: E402
```

5. Save, go back to the **Web** tab and press the big green **Reload** button.

## 7. Check it

- Open `https://username.pythonanywhere.com` → you should get the login page.
- Log in → pick a subject → the question bank should show images.
- If something's wrong, the **Error log** link on the Web tab says why.

---

## Updating the site later

When you've changed the code or imported new papers on your Mac:

```
.venv/bin/python -m scripts.make_deploy_zip --update
```

`--update` leaves your database out, so your saved papers, review decisions and sign-ups
on the server are kept. Then on PythonAnywhere:

1. **Files** tab → upload the new `ExamBuilder.zip` (replace the old one).
2. **Bash** console:

```
unzip -oq ExamBuilder.zip
workon exambuilder
pip install -r ExamBuilder/requirements-server.txt
```

3. If the update changed how questions are cut out (Claude will tell you), re-cut them on the
   server too. This keeps your topics, review decisions and saved papers:

```
cd ~/ExamBuilder && python -m scripts.import_papers --refresh --no-images
```

4. **Web** tab → **Reload**.

New questions you imported on your Mac live in your Mac's database. To copy your whole
database to the server instead, use the normal (non-`--update`) zip; that replaces the
server's database, so first download a backup from the server's Settings page.

## Backups

On the live site, **Settings → Download database backup** gives you a copy of everything
(questions, topics, review decisions, saved papers, sign-ups). Do this now and then.
