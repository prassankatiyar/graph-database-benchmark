# Running this on Windows

The `Makefile` in this repo assumes a Unix `make`, which Windows does not
ship. Use `run.ps1` instead — same targets, same underlying commands.

Everything below assumes you are starting from a Windows machine with nothing
installed.

---

## Before you start: where should the client run?

The benchmark measures round-trip time as well as query time. Three of the
five databases run in Docker on your machine; two are managed services
somewhere on the internet. That is an unavoidable asymmetry, and there are two
ways to handle it.

**Option A — run everything from your Windows machine.** Simplest. The
managed instances will carry your home internet latency (typically 20–80 ms)
on top of every query, while the local containers carry roughly 0.2 ms. That
difference is *measured and published* by the harness (the "TCP RTT" column),
so the results are still honest — but the managed platforms will look slower
than they are, and you must say so plainly in your analysis.

**Option B — run everything from a small cloud VM in the same region as your
managed instances.** More setup, much cleaner numbers. If you have time after
Option A works, this is the upgrade.

Start with Option A. Get real numbers first, then improve them if the clock
allows. A finished honest benchmark beats an unfinished perfect one.

---

## Step 1 — Install Python

1. Go to https://www.python.org/downloads/windows/ and download the latest
   **Windows installer (64-bit)** for Python 3.12.
2. Run it. **Tick "Add python.exe to PATH"** on the first screen — this is the
   single most common thing people miss, and skipping it makes every later
   command fail with "python is not recognized".
3. Click "Install Now".

Open a new **PowerShell** window (press `Win`, type `powershell`, Enter) and
check:

```powershell
python --version
```

You want `Python 3.12.x` or newer. If you get an error, close PowerShell,
reopen it, and try again — PATH changes only apply to new windows.

---

## Step 2 — Install Docker Desktop

1. Download from https://www.docker.com/products/docker-desktop/
2. Run the installer, leave **"Use WSL 2"** ticked, and let it restart your
   machine if it asks.
3. Launch Docker Desktop and wait for the whale icon in your system tray to
   stop animating.

Check it:

```powershell
docker --version
docker compose version
```

**Give Docker enough memory.** Open Docker Desktop → Settings → Resources and
make sure it has at least **4 GB** of RAM available. Three capped containers
plus overhead will not fit in the 2 GB default.

---

## Step 3 — Install Git (optional but recommended)

From https://git-scm.com/download/win — accept every default. You need this to
push to GitHub at the end. If you would rather upload the folder through the
GitHub website, you can skip this.

---

## Step 4 — Set up the project

Put the project folder somewhere simple like `C:\Users\<you>\cognodb-graph-benchmark`,
then in PowerShell:

```powershell
cd C:\Users\<you>\cognodb-graph-benchmark
.\run.ps1 install
```

### If PowerShell refuses to run the script

You will see something about "running scripts is disabled on this system".
That is Windows' default execution policy, not a problem with the file. Allow
local scripts for your user account once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Answer `Y`. Then re-run `.\run.ps1 install`.

`install` creates a `.venv` folder with the pinned dependencies and copies
`.env.example` to `.env` for you.

---

## Step 5 — Prove the harness works before you have any accounts

```powershell
.\run.ps1 test
.\run.ps1 selftest
```

`test` should print `15 passed`. `selftest` runs the entire pipeline — load,
warm-up, percentiles, concurrency sweep — against a synthetic graph and a fake
in-process backend, in about ten seconds.

If both of these pass, the harness is fine and every problem from here on is a
credentials or Docker problem. That is a genuinely useful thing to know before
you start debugging at 2 a.m.

---

## Step 6 — Create the two managed instances

**CognoDB Cloud**

1. Sign up at https://console.cognodb.com/signup (no card required).
2. Create a free **c0** instance and pick a region.
3. **Copy the password immediately.** It is shown exactly once. Paste it into
   Notepad right now.
4. Note the connection URI: `bolt+s://<something>.databases.cognodb.cloud`.
   The username is `cognodb`.

**Neo4j AuraDB Free**

1. Sign up at https://console.neo4j.com and create a **Free** instance.
2. It forces you to download a `.txt` credentials file — keep it.
3. Pick the same cloud region as CognoDB if the option is offered.

---

## Step 7 — Fill in `.env`

Open `.env` in Notepad (`notepad .env` in PowerShell). Fill in the real values:

```ini
COGNODB_URI=bolt+s://abc123.databases.cognodb.cloud
COGNODB_USER=cognodb
COGNODB_PASSWORD=your-actual-password
COGNODB_DATABASE=neo4j

AURA_URI=neo4j+s://def456.databases.neo4j.io
AURA_USER=neo4j
AURA_PASSWORD=your-actual-password
AURA_DATABASE=neo4j

MEMGRAPH_URI=bolt://localhost:7688
MEMGRAPH_USER=
MEMGRAPH_PASSWORD=
MEMGRAPH_DATABASE=memgraph

FALKORDB_HOST=localhost
FALKORDB_PORT=6379
FALKORDB_PASSWORD=
FALKORDB_GRAPH=bench

ARANGO_URI=http://localhost:8529
ARANGO_USER=root
ARANGO_PASSWORD=pick-any-password-here
ARANGO_DATABASE=bench
```

Notes:

- Memgraph and FalkorDB have no password by default. Leave those blank.
- `ARANGO_PASSWORD` is one **you** invent — Docker will set it on the
  container at first start. It cannot be blank.
- Use `localhost` for the three Docker services because they run on your
  machine.
- **`.env` is gitignored and must never be committed.** The assignment
  explicitly checks for this.

---

## Step 8 — Start the three self-hosted databases

```powershell
.\run.ps1 up
```

First run pulls about 1.5 GB of images — give it a few minutes.

The script then prints a `docker stats` table. **Read it.** The `MEM USAGE`
column must show a limit of `256MiB` for all three containers:

```
NAME              CPU %     MEM USAGE / LIMIT
bench-memgraph    0.30%     48.2MiB / 256MiB
bench-falkordb    0.15%     12.1MiB / 256MiB
bench-arangodb    1.20%     91.7MiB / 256MiB
```

If the limit column shows your full system RAM instead, the cap did not apply
and every number you produce afterward is meaningless. Fix that before going
further.

Also confirm FalkorDB honoured its memory setting:

```powershell
docker exec bench-falkordb redis-cli CONFIG GET maxmemory
```

You want `241172480`, not `0`.

---

## Step 9 — Prepare the dataset

```powershell
.\run.ps1 dataset
```

This downloads the citation network, snowball-samples it to 200,000
relationships, and freezes the shared start-node pool.

**Run this exactly once.** Every platform must be loaded from the same frozen
CSVs, or the comparison is invalid — the report generator will refuse to
publish tables where two platforms have different dataset hashes.

---

## Step 10 — Check every connection before committing 45 minutes

```powershell
.\run.ps1 doctor
```

You want five `connect OK` lines. Fix anything that fails here — see
troubleshooting below.

---

## Step 11 — Run the benchmark

```powershell
.\run.ps1 bench
```

This takes roughly 30–45 minutes for all five platforms. Leave the machine
alone while it runs — if you start a game or a video call, you are adding
noise to your own measurements and the variance column will show it.

If one platform fails partway through, the others still complete and their
results are saved. You can re-run just the broken one:

```powershell
.\.venv\Scripts\python.exe -m bench run --platform arangodb -v
```

---

## Step 12 — Generate the results

```powershell
.\run.ps1 report
```

This writes `RESULTS.md`, draws four charts into `results\charts\`, and
splices the tables into `README.md` automatically. Do not type those tables in
by hand.

---

## Step 13 — Check the results before believing them

Open `RESULTS.md` and read these two sections **first**:

- **§6 result-set parity.** The median rows returned per workload should be
  the same across platforms. If one returns 12 rows for a 3-hop query and
  another returns 1,180, the query translation is wrong and every latency
  above it is meaningless.
- **§7 run-to-run variance.** If the coefficient of variation is 15% and two
  platforms differ by 5%, they are tied. Do not write "X is faster than Y"
  about a gap smaller than the noise.

Then write your Analysis section in `README.md` using your real numbers, and
fill in the results section of `ARTICLE.md`.

---

## Step 14 — Push to GitHub

```powershell
git init
git add .
git commit -m "Graph database cloud benchmark"
```

Create an empty repository on github.com, then:

```powershell
git remote add origin https://github.com/<your-username>/<repo-name>.git
git branch -M main
git push -u origin main
```

**Before you push, verify no secrets are going up:**

```powershell
git status --porcelain
```

`.env` must not appear in that list. It is in `.gitignore`, but check anyway —
this is the one mistake in this assignment that cannot be undone by editing a
file afterward.

Then replace the `YOUR-USERNAME/YOUR-REPO` placeholders in `ARTICLE.md`, and
email the repository URL to hr@wexa.ai with the subject line
`CognoDB Assignment 1 – <Your Name>`.

---

## Troubleshooting

### "python is not recognized"
You skipped "Add python.exe to PATH" during install. Re-run the Python
installer, choose **Modify**, and tick it. Then open a *new* PowerShell.

### "running scripts is disabled on this system"
See Step 4 above — it is the execution policy.

### `docker compose` says `ARANGO_PASSWORD` is not set
You are running compose without `--env-file .env`. Use `.\run.ps1 up`, which
passes it. If you are calling docker directly, the full command is:

```powershell
docker compose --env-file .env -f docker/docker-compose.yml up -d
```

### `bench-arangodb` keeps restarting
ArangoDB is the heaviest of the three engines and 256 MB is genuinely tight
for it. Check why:

```powershell
docker logs bench-arangodb --tail 50
```

If it is being OOM-killed, you have a real methodology decision to make, and
either answer is defensible as long as you write it down:

- **Report it as a finding.** "ArangoDB would not run within the CognoDB c0
  resource budget" is a legitimate, interesting result. Document it, drop it
  from the latency tables, and pick a fifth database that does fit.
- **Raise the floor for everyone.** But note that you *cannot* raise CognoDB
  above its free tier, so this breaks parity in the other direction. This is
  the worse option.

What you must not do is quietly give ArangoDB 1 GB and leave the others at
256 MB.

### `doctor` fails on CognoDB or Aura with an authentication error
Re-check the password in `.env` for stray spaces or a missing character. Both
services show the password once; if you lost it, reset it from the console
rather than guessing.

### `doctor` fails on Memgraph/FalkorDB/ArangoDB with a connection refused
The containers are not up, or Docker Desktop is not running. Check:

```powershell
docker ps
```

You should see three containers. If not, `.\run.ps1 up`.

### Aura rejects the load with a node or relationship limit error
AuraDB Free caps at 200k nodes / 400k relationships. Our sample is 200k
relationships, which fits — but if you changed `TARGET_EDGES` in
`bench/config.py`, lower it back. Whatever you choose must be the same for
every platform.

### A 3-hop query times out on one platform
That is a result, not a bug. The harness counts it, records the exception, and
prints it in §10 of `RESULTS.md`. Leave it in and discuss it in your analysis —
"this platform could not complete 3-hop traversals within 30 s at this tier"
is one of the more interesting things the benchmark can tell you.

### The numbers look wildly different between two runs
Expected on a burstable 0.5-vCPU tier, and exactly why the variance column
exists. Close other applications, avoid running the benchmark over Wi-Fi if
you can use Ethernet, and let the three repeats do their job.
