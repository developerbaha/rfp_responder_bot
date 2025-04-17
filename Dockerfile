FROM python:3.12-slim

RUN useradd -m -u 1000 chameleon

USER chameleon

ENV PATH="/home/chameleon/.local/bin:$PATH"

WORKDIR /app

COPY --chown=chameleon requirements.txt requirements.txt

RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY --chown=chameleon . .

# HuggingFace Space port
ENV PORT=7860

# Expose the port
EXPOSE 7860

# Start command for loop
# CMD ["sh", "-c", "python bot_telegram.py & tail -f /dev/null"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]