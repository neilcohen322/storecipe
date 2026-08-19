from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

CATALOG_SCHEMA = "catalog"


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = ({"schema": CATALOG_SCHEMA},)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    auth_subject: Mapped[str] = mapped_column(String(255), unique=True)
    catalog_version: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    recipes: Mapped[list["Recipe"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    ratings: Mapped[list["Rating"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    recipe_creation_idempotency_records: Mapped[list["RecipeCreationIdempotency"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Recipe(Base):
    __tablename__ = "recipes"
    __table_args__ = (
        UniqueConstraint("user_id", "import_job_id", name="uq_recipes_user_import_job"),
        CheckConstraint("servings IS NULL OR servings > 0", name="positive_servings"),
        CheckConstraint("prep_minutes IS NULL OR prep_minutes >= 0", name="nonnegative_prep"),
        CheckConstraint("cook_minutes IS NULL OR cook_minutes >= 0", name="nonnegative_cook"),
        CheckConstraint("total_minutes IS NULL OR total_minutes >= 0", name="nonnegative_total"),
        Index(
            "ix_recipes_user_source_fingerprint",
            "user_id",
            "source_fingerprint",
            postgresql_where=text("source_fingerprint IS NOT NULL"),
        ),
        {"schema": CATALOG_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{CATALOG_SCHEMA}.users.id", ondelete="CASCADE"), index=True
    )
    import_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    servings: Mapped[int | None] = mapped_column(nullable=True)
    prep_minutes: Mapped[int | None] = mapped_column(nullable=True)
    cook_minutes: Mapped[int | None] = mapped_column(nullable=True)
    total_minutes: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    user: Mapped[User] = relationship(back_populates="recipes")
    ingredients: Mapped[list["Ingredient"]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="Ingredient.position",
    )
    instructions: Mapped[list["Instruction"]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="Instruction.position",
    )
    recipe_tags: Mapped[list["RecipeTag"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )
    ratings: Mapped[list["Rating"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )
    recipe_creation_idempotency: Mapped["RecipeCreationIdempotency | None"] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", single_parent=True
    )
    cover_image: Mapped["RecipeImage | None"] = relationship(
        back_populates="recipe",
        uselist=False,
        cascade="all, delete-orphan",
        single_parent=True,
    )


class RecipeCreationIdempotency(Base):
    __tablename__ = "recipe_creation_idempotency"
    __table_args__ = (
        UniqueConstraint("recipe_id"),
        CheckConstraint("length(payload_hash) = 64", name="payload_hash_length"),
        {"schema": CATALOG_SCHEMA},
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{CATALOG_SCHEMA}.users.id", ondelete="CASCADE"), primary_key=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    recipe_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{CATALOG_SCHEMA}.recipes.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="recipe_creation_idempotency_records")
    recipe: Mapped[Recipe] = relationship(back_populates="recipe_creation_idempotency")


class RecipeImage(Base):
    __tablename__ = "recipe_images"
    __table_args__ = (
        UniqueConstraint("recipe_id", name="uq_recipe_images_recipe_id"),
        UniqueConstraint("object_key", name="uq_recipe_images_object_key"),
        CheckConstraint("content_type = 'image/webp'", name="ck_recipe_images_webp"),
        CheckConstraint(
            "byte_size > 0 AND byte_size <= 1572864",
            name="ck_recipe_images_byte_size",
        ),
        CheckConstraint("length(sha256) = 64", name="ck_recipe_images_sha256"),
        {"schema": CATALOG_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    recipe_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{CATALOG_SCHEMA}.recipes.id", ondelete="CASCADE")
    )
    object_key: Mapped[str] = mapped_column(String(512))
    object_generation: Mapped[str] = mapped_column(String(32))
    content_type: Mapped[str] = mapped_column(String(32), default="image/webp")
    byte_size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    recipe: Mapped[Recipe] = relationship(back_populates="cover_image")


class Ingredient(Base):
    __tablename__ = "ingredients"
    __table_args__ = (
        UniqueConstraint("recipe_id", "position", name="uq_ingredients_recipe_position"),
        CheckConstraint("position >= 0", name="nonnegative_position"),
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="nonnegative_quantity"),
        Index("ix_ingredients_normalized_name_recipe", "normalized_name", "recipe_id"),
        Index("ix_ingredients_canonical_name_recipe", "canonical_name", "recipe_id"),
        {"schema": CATALOG_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    recipe_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{CATALOG_SCHEMA}.recipes.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int]
    raw_text: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200))
    canonical_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)

    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")


class Instruction(Base):
    __tablename__ = "instructions"
    __table_args__ = (
        UniqueConstraint("recipe_id", "position", name="uq_instructions_recipe_position"),
        CheckConstraint("position >= 0", name="nonnegative_position"),
        {"schema": CATALOG_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    recipe_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{CATALOG_SCHEMA}.recipes.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int]
    text: Mapped[str] = mapped_column(Text)

    recipe: Mapped[Recipe] = relationship(back_populates="instructions")


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = ({"schema": CATALOG_SCHEMA},)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True)

    recipe_tags: Mapped[list["RecipeTag"]] = relationship(
        back_populates="tag", cascade="all, delete-orphan"
    )


class RecipeTag(Base):
    __tablename__ = "recipe_tags"
    __table_args__ = ({"schema": CATALOG_SCHEMA},)

    recipe_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{CATALOG_SCHEMA}.recipes.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{CATALOG_SCHEMA}.tags.id", ondelete="CASCADE"), primary_key=True
    )

    recipe: Mapped[Recipe] = relationship(back_populates="recipe_tags")
    tag: Mapped[Tag] = relationship(back_populates="recipe_tags")


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (
        CheckConstraint("value BETWEEN 1 AND 5", name="value_range"),
        {"schema": CATALOG_SCHEMA},
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{CATALOG_SCHEMA}.users.id", ondelete="CASCADE"), primary_key=True
    )
    recipe_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{CATALOG_SCHEMA}.recipes.id", ondelete="CASCADE"), primary_key=True
    )
    value: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    user: Mapped[User] = relationship(back_populates="ratings")
    recipe: Mapped[Recipe] = relationship(back_populates="ratings")
