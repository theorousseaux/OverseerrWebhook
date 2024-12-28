FROM python:3.9

WORKDIR /app

COPY . /app

RUN pip install -r requirements.txt

# Only necessary if overseerr runs elsewhere than in this docker compose file 
# EXPOSE 5056

CMD ["python", "-u", "webhook_listener.py"]