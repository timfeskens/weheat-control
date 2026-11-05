"""PKCE login and installation selection through the Home Assistant UI."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuthenticationError, Installation, Tokens, WeheatApi, WeheatError, callback_code, login_request
from .const import BOARD_ID, DOMAIN, INSTALLATION_ID, TOKENS


class WeheatConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._url = ""
        self._verifier = ""
        self._state = ""
        self._api: WeheatApi | None = None
        self._installations: list[Installation] = []

    async def async_step_user(self, user_input: dict[str, str] | None = None):
        """Generate PKCE once per login attempt; don't collect a password."""
        self._url, self._verifier, self._state = login_request()
        return await self.async_step_login()

    async def async_step_login(self, user_input: dict[str, str] | None = None):
        errors: dict[str, str] = {}
        if user_input:
            try:
                code = callback_code(user_input["callback_url"], self._state)
                self._api = WeheatApi(async_get_clientsession(self.hass))
                await self._api.exchange(code, self._verifier)
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except WeheatError:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_installation()
            # Authorization codes are one-use; offer a fresh URL after failure.
            self._url, self._verifier, self._state = login_request()
        return self.async_show_form(
            step_id="login",
            data_schema=vol.Schema({vol.Required("callback_url"): str}),
            errors=errors,
            description_placeholders={"login_url": self._url},
        )

    async def async_step_installation(self, user_input: dict[str, str] | None = None):
        """Keep exchanged tokens in this flow when discovery fails."""
        if self._api is None:
            return self.async_abort(reason="invalid_auth")
        errors: dict[str, str] = {}
        try:
            self._installations = await self._api.installations()
        except AuthenticationError:
            return self.async_abort(reason="invalid_auth")
        except WeheatError:
            errors["base"] = "cannot_connect"
        if not errors:
            if not self._installations:
                return self.async_abort(reason="no_devices")
            selected = (
                self._installations[0]
                if len(self._installations) == 1
                else next(
                    (item for item in self._installations if user_input and item.id == user_input.get(INSTALLATION_ID)),
                    None,
                )
            )
            if selected and self._api.tokens:
                return await self._finish(selected, self._api.tokens)
        choices = {item.id: f"Installation {item.id}" for item in self._installations}
        return self.async_show_form(
            step_id="installation",
            errors=errors,
            data_schema=vol.Schema({vol.Required(INSTALLATION_ID): vol.In(choices)}) if choices else vol.Schema({}),
        )

    async def _finish(self, installation: Installation, tokens: Tokens):
        await self.async_set_unique_id(installation.id)
        if self.source == config_entries.SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            if entry.data[INSTALLATION_ID] != installation.id:
                return self.async_abort(reason="wrong_account")
            return self.async_update_reload_and_abort(
                entry, data_updates={TOKENS: tokens.as_dict(), BOARD_ID: installation.board_id}
            )
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Weheat {installation.id}",
            data={INSTALLATION_ID: installation.id, BOARD_ID: installation.board_id, TOKENS: tokens.as_dict()},
        )

    async def async_step_reauth(self, entry_data: dict[str, object]):
        return await self.async_step_user()
