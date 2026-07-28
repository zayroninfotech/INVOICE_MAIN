from datetime import datetime


def generate_invoice_number(last_number: int = 0) -> str:
    year = datetime.now().year
    return f"INV-{year}-{str(last_number + 1).zfill(4)}"
