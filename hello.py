import smtplib
import ssl
from email.message import EmailMessage
import os

from dotenv import load_dotenv


def main():

    load_dotenv()

    SMTP_HOST = os.environ["SMTP_HOST"]
    SMTP_PORT = int(os.environ["SMTP_PORT"])
    SENDER_EMAIL = os.environ["SENDER_EMAIL"]
    APP_PASSWORD = os.environ["APP_PASSWORD"]

    # Create the email
    msg = EmailMessage()
    msg["Subject"] = "Powiadomienie z bota / Newsletter"
    msg["From"] = SENDER_EMAIL
    msg["To"] = "m-mazur@wp.pl"
    msg.set_content("Witaj! To jest automatyczna wiadomosc wyslana z bota.")

    # Connect using SSL on port 465
    context = ssl.create_default_context()

    try:
        print(f"Łączenie z serwerem SMTP: {SMTP_HOST}:{SMTP_PORT}...", end=" ", flush=True)
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
            print("Połączono!")
            print("Logowanie...", end=" ", flush=True)
            server.login(SENDER_EMAIL, APP_PASSWORD)
            print("Zalogowano!")
            print("Wysyłanie wiadomości...", end=" ", flush=True)
            server.send_message(msg)
        print("Wiadomość została wysłana pomyślnie!")
    except Exception as e:
        print(f"Błąd podczas wysyłania: {e}")


if __name__ == "__main__":
    main()
