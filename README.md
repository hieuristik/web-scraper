# web-scraper
Web Scraping Repo for Dev Contest Submission

## Setup
After successfully cloning the repo into your local system, navigate to the location of the project and enter the following into your CLI of choice:


For those using Windows:
```
.venv\Scripts\activate
```
For those on Linux/MacOS:
```
source .venv/bin/activate
```


After successfully setting up your virtual environment, run the following command in order to install the necessary dependencies:
```
pip install -r requirements.txt
```


## How to Run (Local, Non-Docker)
Upon successful completion of the setup steps, issue the following command into your terminal of choice:
```
python -m src.cli --origin LAX --destination JFK --date 2025-12-15
```

You should notice an **output.json** nestled within: *data/processed*


## Docker
Issue the following command into your terminal of choice in order to pull the latest image from Docker Hub:
```
docker pull hieuristik/aa-scraper:latest
```
Once the image has successfully been pulled, issue the following command in order to activate a demo run of the crawler:
```
docker run --rm -v ${PWD}/data/processed:/app/data/processed -v ${PWD}/data/debug:/app/data/debug \
  --entrypoint python hieuristik/aa-scraper:latest /app/src/demo_mode.py \
  --cash-file /app/data/debug/cash_results_rendered.html --award-file /app/data/debug/award_results_rendered.html --origin LAX --destination JFK --date 2025-12-15
```
In order to take a look at the program's output, issue the following command into the terminal:
```
Get-Content .\data\processed\output.json -Raw | Out-String
```
