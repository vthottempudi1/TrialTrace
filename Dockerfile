# TrialTrace: one image for the API and the dashboard (compose picks the command).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# dependencies first, so a code change does not reinstall them
COPY pyproject.toml ./
COPY trialtrace ./trialtrace
RUN pip install ".[api,dashboard]"

COPY dashboard ./dashboard
COPY .streamlit/config.toml ./.streamlit/config.toml
COPY evals/validation_v1/assisted_review ./evals/validation_v1/assisted_review

RUN useradd --create-home trialtrace && mkdir -p /app/data && chown -R trialtrace /app
USER trialtrace

ENV PORT=8501
EXPOSE 8501
# the database address and keys come from the environment, never from the image
# Cloud Run supplies PORT; locally it defaults to Streamlit's usual port.
CMD ["sh", "-c", "exec streamlit run dashboard/app.py --server.address=0.0.0.0 --server.port=${PORT} --server.headless=true"]
