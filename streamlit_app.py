"""HealthAI Streamlit entry point.

Run locally with:

    streamlit run streamlit_app.py
"""

from app.ui.dashboard import render_dashboard


def main() -> None:
    render_dashboard()


if __name__ == "__main__":
    main()
