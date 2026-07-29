import asyncio
from uuid import uuid4

import httpx
import jwt
import pytest
from services.ingestion.tests.integration.fake_stack import app


@pytest.mark.asyncio
async def test_fake_stack_issues_tokens_and_deduplicates_catalog_retries() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://fake-deps") as client:
        token_response = await client.get("/test/access-token")
        assert token_response.status_code == 200
        token = token_response.json()["access_token"]
        claims = jwt.decode(token, options={"verify_signature": False})
        assert claims["iss"] == "http://fake-deps:8080/"
        assert claims["aud"] == "https://storecipe.test"

        job_id = str(uuid4())
        payload = {"importJobId": job_id, "ownerSubject": "auth0|week8", "title": "Soup"}
        request = asyncio.create_task(
            client.post(
                "/internal/recipes/imported",
                headers={"Authorization": "Bearer fake-m2m-token"},
                json=payload,
            )
        )

        for _ in range(50):
            status = await client.get(f"/test/catalog/{job_id}")
            if status.json()["arrivals"] == 1:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("fake Catalog request did not arrive")

        await client.post(f"/test/catalog/{job_id}/release")
        first = await request
        second = await client.post(
            "/internal/recipes/imported",
            headers={"Authorization": "Bearer fake-m2m-token"},
            json=payload,
        )

        assert first.status_code == 201
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        status = (await client.get(f"/test/catalog/{job_id}")).json()
        assert status["arrivals"] == 2
        assert status["creations"] == 1
