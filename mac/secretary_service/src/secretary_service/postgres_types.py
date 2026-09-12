"""Shared PostgreSQL connection typing for queries returning text columns."""

import psycopg

type PgConnection = psycopg.Connection[tuple[str, ...]]
