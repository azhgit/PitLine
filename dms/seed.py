"""Reset the mock DMS DB and load fixed demo data. Run explicitly: python seed.py"""

from datetime import datetime, timedelta

from db import Base, SessionLocal, engine
from models import Appointment, Technician

TODAY = datetime.now().replace(minute=0, second=0, microsecond=0)


def seed():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        mike = Technician(name="Mike Alvarez", specialty="quick_service", on_call=True)
        dana = Technician(name="Dana Whitfield", specialty="master_technician", on_call=True)
        ravi = Technician(name="Ravi Patel", specialty="quick_service", on_call=False)
        db.add_all([mike, dana, ravi])
        db.commit()

        # (technician, day_offset, hour, customer, phone, make, model, year)
        # scattered so real gaps exist
        bookings = [
            (mike, 1, 9, "Carla Nguyen", "555-0101", "Honda", "Civic", 2019),
            (mike, 1, 14, "Tom Reyes", "555-0102", "Toyota", "Camry", 2021),
            (mike, 3, 10, "Ella Brooks", "555-0103", "Ford", "F-150", 2018),
            (dana, 2, 11, "Jon Park", "555-0104", "Subaru", "Outback", 2020),
            (dana, 4, 9, "Priya Shah", "555-0105", "Honda", "CR-V", 2022),
            (dana, 5, 15, "Ben Hughes", "555-0106", "Chevrolet", "Malibu", 2017),
            (ravi, 2, 13, "Nora Kim", "555-0107", "Mazda", "CX-5", 2023),
        ]
        for tech, day_offset, hour, name, phone, make, model, year in bookings:
            db.add(
                Appointment(
                    technician_id=tech.id,
                    time_slot=TODAY.replace(hour=hour) + timedelta(days=day_offset),
                    customer_name=name,
                    customer_phone=phone,
                    vehicle_make=make,
                    vehicle_model=model,
                    vehicle_year=year,
                    urgency="non_urgent",
                    status="booked",
                )
            )
        db.commit()
        print("Seeded 3 technicians and 7 appointments.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
