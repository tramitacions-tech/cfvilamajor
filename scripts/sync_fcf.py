import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright


BASE_URL = "https://www.fcf.cat"
CLUB_ID = "1096"
CLUB_URL = f"{BASE_URL}/ca/clubs/{CLUB_ID}/categories/39706"

OUTPUT = Path("data/fcf.json")
DEBUG = Path("data/fcf-debug.json")


def clean(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


async def main():

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    responses = []
    requests = []
    console_messages = []
    errors = []

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            viewport={
                "width": 1440,
                "height": 1200
            },
            locale="ca-ES"
        )

        # -----------------------------------------
        # CAPTURAR PETICIONS
        # -----------------------------------------

        async def capture_request(request):
            try:
                url = request.url

                if "fcf.cat" not in url:
                    return

                resource_type = request.resource_type

                if resource_type in [
                    "xhr",
                    "fetch",
                    "document",
                    "script"
                ]:
                    requests.append({
                        "method": request.method,
                        "url": url,
                        "resource_type": resource_type
                    })

            except Exception:
                pass


        # -----------------------------------------
        # CAPTURAR RESPOSTES
        # -----------------------------------------

        async def capture_response(response):

            try:

                url = response.url

                if "fcf.cat" not in url:
                    return

                resource_type = response.request.resource_type

                if resource_type not in [
                    "xhr",
                    "fetch",
                    "document"
                ]:
                    return

                content_type = (
                    response.headers.get(
                        "content-type",
                        ""
                    ).lower()
                )

                # Només ens interessen respostes
                # que puguin contenir dades
                if not any(
                    x in content_type
                    for x in [
                        "json",
                        "text",
                        "javascript"
                    ]
                ):
                    return

                try:

                    body = await response.text()

                    if len(body) > 3000000:
                        body = body[:3000000]

                    responses.append({
                        "url": url,
                        "status": response.status,
                        "resource_type": resource_type,
                        "content_type": content_type,
                        "body": body
                    })

                except Exception as exc:

                    responses.append({
                        "url": url,
                        "status": response.status,
                        "resource_type": resource_type,
                        "content_type": content_type,
                        "body_error": str(exc)
                    })

            except Exception:
                pass


        page.on("request", capture_request)
        page.on("response", capture_response)

        page.on(
            "console",
            lambda msg: console_messages.append({
                "type": msg.type,
                "text": msg.text
            })
        )

        page.on(
            "pageerror",
            lambda exc: errors.append(str(exc))
        )

        print("==========================================")
        print("FCF VILAMAJOR - DIAGNÒSTIC")
        print("==========================================")
        print(CLUB_URL)
        print("")


        # -----------------------------------------
        # OBRIR FCF
        # -----------------------------------------

        try:

            await page.goto(
                CLUB_URL,
                wait_until="domcontentloaded",
                timeout=90000
            )

        except Exception as exc:

            print("AVÍS page.goto:")
            print(exc)


        print("Esperant que FCF carregui les dades...")
        await page.wait_for_timeout(15000)


        # -----------------------------------------
        # SCROLL
        # -----------------------------------------

        await page.evaluate(
            """
            async () => {

                for (let i = 0; i < 10; i++) {

                    window.scrollTo(
                        0,
                        document.body.scrollHeight
                    );

                    await new Promise(
                        resolve => setTimeout(resolve, 700)
                    );
                }

                window.scrollTo(0, 0);
            }
            """
        )


        await page.wait_for_timeout(5000)


        # -----------------------------------------
        # INFORMACIÓ DEL DOM
        # -----------------------------------------

        visible_text = clean(
            await page.locator("body").inner_text()
        )

        html = await page.content()


        # -----------------------------------------
        # LINKS
        # -----------------------------------------

        anchors = []

        for a in await page.locator("a").all():

            try:

                href = await a.get_attribute("href")
                text = clean(
                    await a.inner_text()
                )

                if href:

                    anchors.append({
                        "text": text,
                        "href": href
                    })

            except Exception:
                pass


        # -----------------------------------------
        # JAVASCRIPT GLOBAL
        # -----------------------------------------

        globals_found = await page.evaluate(
            """
            () => {

                const result = {};

                for (const key of Object.keys(window)) {

                    try {

                        const value = window[key];

                        if (
                            typeof value === "object" &&
                            value !== null
                        ) {

                            const text =
                                JSON.stringify(value);

                            if (
                                text &&
                                (
                                    text.includes("VILAMAJOR") ||
                                    text.includes("JUVENIL") ||
                                    text.includes("CADET") ||
                                    text.includes("INFANTIL") ||
                                    text.includes("ALEV") ||
                                    text.includes("BENJ") ||
                                    text.includes("PREBENJ") ||
                                    text.includes("FEMEN")
                                )
                            ) {

                                result[key] = text.substring(
                                    0,
                                    100000
                                );
                            }
                        }

                    } catch (e) {}
                }

                return result;
            }
            """
        )


        # -----------------------------------------
        # GUARDAR DIAGNÒSTIC
        # -----------------------------------------

        debug = {

            "generated_at":
                datetime.now().astimezone().isoformat(),

            "club_url":
                CLUB_URL,

            "page_title":
                await page.title(),

            "visible_text":
                visible_text,

            "anchors":
                anchors,

            "requests":
                requests,

            "responses":
                responses,

            "console":
                console_messages,

            "page_errors":
                errors,

            "window_globals":
                globals_found,

            "html":
                html[:3000000]
        }


        DEBUG.write_text(
            json.dumps(
                debug,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )


        # -----------------------------------------
        # NO TOCAR FCF.JSON
        # -----------------------------------------

        print("")
        print("==========================================")
        print("DIAGNÒSTIC FINALITZAT")
        print("==========================================")
        print(
            f"Peticion(s) FCF: {len(requests)}"
        )
        print(
            f"Resposta(s) FCF: {len(responses)}"
        )
        print(
            f"Enllaços: {len(anchors)}"
        )
        print(
            f"Globals: {len(globals_found)}"
        )
        print("")
        print(
            "S'ha creat: data/fcf-debug.json"
        )
        print("")
        print(
            "IMPORTANT: data/fcf.json NO s'ha modificat."
        )


        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
