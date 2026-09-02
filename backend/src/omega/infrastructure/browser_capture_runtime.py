import contextlib
import hashlib

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Route,
    async_playwright,
)
from pydantic import BaseModel, ConfigDict

from omega.application.visual_direction import VisualTemplateId
from omega.application.visual_template_renderer import RenderedTemplateDocument


class BrowserCapturedFrame(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_index: int
    template_id: VisualTemplateId
    width: int
    height: int
    png_bytes: bytes
    png_sha256: str
    source_html_sha256: str


class BrowserCaptureError(ValueError):
    pass


class BrowserCaptureRuntime:
    def __init__(self):
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._started = False

    async def __aenter__(self):
        try:
            self._playwright_ctx_mgr = async_playwright()
            self._playwright = await self._playwright_ctx_mgr.__aenter__()
            self._browser = await self._playwright.chromium.launch(headless=True)
            self._context = await self._browser.new_context(
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=1,
                locale="en-US",
                timezone_id="UTC",
                color_scheme="dark",
                reduced_motion="reduce",
                java_script_enabled=False,
            )

            async def abort_external_requests(route: Route):
                url = route.request.url
                if url.startswith("http://") or url.startswith("https://"):
                    await route.abort()
                else:
                    await route.continue_()

            await self._context.route("**/*", abort_external_requests)
            self._page = await self._context.new_page()
            self._started = True
            return self
        except Exception as e:
            await self._cleanup()
            raise BrowserCaptureError(f"Failed to start browser runtime: {e}") from e

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._cleanup()

    async def _cleanup(self):
        self._started = False
        if self._page:
            with contextlib.suppress(Exception):
                await self._page.close()
            self._page = None
        if self._context:
            with contextlib.suppress(Exception):
                await self._context.close()
            self._context = None
        if self._browser:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if hasattr(self, "_playwright_ctx_mgr"):
            with contextlib.suppress(Exception):
                await self._playwright_ctx_mgr.__aexit__(None, None, None)

    async def capture(
        self,
        document: RenderedTemplateDocument,
        transparent_background: bool = False,
    ) -> BrowserCapturedFrame:
        if not self._started or not self._page:
            raise BrowserCaptureError("Browser runtime not started.")

        try:
            await self._page.set_content(document.html)
            screenshot_kwargs = {
                "type": "png",
                "full_page": False,
                "animations": "disabled",
            }
            if transparent_background:
                screenshot_kwargs["omit_background"] = True

            png_bytes = await self._page.screenshot(**screenshot_kwargs)
        except Exception as e:
            raise BrowserCaptureError(f"Failed to capture document: {e}") from e

        if not png_bytes:
            raise BrowserCaptureError("Screenshot returned empty bytes.")

        if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise BrowserCaptureError("Screenshot bytes lack PNG signature.")

        if len(png_bytes) < 24:
            raise BrowserCaptureError("Screenshot bytes too short to contain IHDR.")

        ihdr_type = png_bytes[12:16]
        if ihdr_type != b"IHDR":
            raise BrowserCaptureError("First PNG chunk is not IHDR.")

        width = int.from_bytes(png_bytes[16:20], byteorder="big")
        height = int.from_bytes(png_bytes[20:24], byteorder="big")

        if width != 1920 or height != 1080:
            raise BrowserCaptureError(f"Screenshot dimensions {width}x{height} != 1920x1080.")

        png_sha256 = hashlib.sha256(png_bytes).hexdigest()

        return BrowserCapturedFrame(
            scene_index=document.scene_index,
            template_id=document.template_id,
            width=width,
            height=height,
            png_bytes=png_bytes,
            png_sha256=png_sha256,
            source_html_sha256=document.content_sha256,
        )
