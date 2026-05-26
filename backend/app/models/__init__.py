# Re-export all models so that importing this package is enough to register
# every table with SQLAlchemy's metadata — required by Alembic autogenerate.
from app.models.garden_request import GardenRequest
from app.models.recommendation import Recommendation
from app.models.species import Species

__all__ = ["Species", "GardenRequest", "Recommendation"]
