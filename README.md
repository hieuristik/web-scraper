# web-scraper
Web Scraping Repo for Dev Contest Submission

## How to Run
Copy and paste the following into your terminal of choice

```
python -m src.cli --origin LAX --destination JFK --date 2025-12-15
```

```
docker pull hieuristik/aa-scraper:latest
```

```
docker run --rm -v ${PWD}/data/processed:/app/data/processed -v ${PWD}/data/debug:/app/data/debug \
  --entrypoint python hieuristik/aa-scraper:latest /app/src/demo_mode.py \
  --cash-file /app/data/debug/cash_results_rendered.html --award-file /app/data/debug/award_results_rendered.html --origin LAX --destination JFK --date 2025-12-15
```

```
Get-Content .\data\processed\output.json -Raw | Out-String
```