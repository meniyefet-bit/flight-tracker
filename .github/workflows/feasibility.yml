name: Feasibility check

on:
  workflow_dispatch:

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          pip install playwright fast-flights
          playwright install --with-deps chromium

      - name: Run feasibility check
        run: python feasibility.py

      - name: Save screenshots and results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: feasibility-results
          path: out/
