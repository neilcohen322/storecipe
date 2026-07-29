CREATE ROLE catalog_app LOGIN PASSWORD 'local_catalog_only';
CREATE ROLE ingestion_app LOGIN PASSWORD 'local_ingestion_only';

CREATE SCHEMA catalog AUTHORIZATION catalog_app;
CREATE SCHEMA ingestion AUTHORIZATION ingestion_app;

GRANT CONNECT ON DATABASE storecipe TO catalog_app, ingestion_app;
GRANT CREATE ON DATABASE storecipe TO catalog_app, ingestion_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
