# Audit Intelligence

A Streamlit executive newsroom for global banking audit, risk, controls and regulatory intelligence.

## Features

- NewsData.io as the only news provider
- Four fixed themes: Transformation, Regulation, People and Global Banks
- Two-card editorial grid with responsive mobile layout
- Server-side API credential; never displayed in the UI
- Duplicate reduction and audit-relevance filtering
- Search, lookback window and CSV export
- Dark, futuristic executive interface

## Streamlit deployment

1. Push this repository to GitHub.
2. In Streamlit Community Cloud, create a new app from this repository.
3. Set the main file to `app.py`.
4. In **App settings -> Secrets**, add:

```toml
NEWSDATA_API_KEY = "your_newsdata_key"
```

Do not commit the key to GitHub.

## Local run

```bash
pip install -r requirements.txt
streamlit run app.py
```

The application reads `NEWSDATA_API_KEY` from Streamlit secrets or the server environment.


<!-- Streamlit fix trigger -->
