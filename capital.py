"""
Capital.com API client
Handles authentication, opening/closing positions
"""

import time
import logging
import requests

log = logging.getLogger(__name__)

BASE_URLS = {
    "demo": "https://demo-api-capital.backend-capital.com",
    "live": "https://api-capital.backend-capital.com"
}


class CapitalClient:
    def __init__(self, api_key, password, account_id, env="demo"):
        self.api_key        = api_key
        self.password       = password
        self.account_id     = account_id
        self.base_url       = BASE_URLS.get(env, BASE_URLS["demo"])
        self.cst            = None
        self.security_token = None
        self._authenticate()

    def _authenticate(self):
        url     = f"{self.base_url}/api/v1/session"
        headers = {
            "X-CAP-API-KEY": self.api_key,
            "Content-Type":  "application/json"
        }
        body = {
            "identifier":        self.account_id,
            "password":          self.password,
            "encryptedPassword": False
        }
        resp = requests.post(url, json=body, headers=headers, timeout=10)
        resp.raise_for_status()
        self.cst            = resp.headers.get("CST")
        self.security_token = resp.headers.get("X-SECURITY-TOKEN")
        log.info("Capital.com: session authenticated successfully")

    def _headers(self):
        return {
            "CST":              self.cst,
            "X-SECURITY-TOKEN": self.security_token,
            "Content-Type":     "application/json"
        }

    def _request(self, method, path, **kwargs):
        url = f"{self.base_url}{path}"
        for attempt in range(2):
            try:
                resp = requests.request(
                    method, url, headers=self._headers(), timeout=10, **kwargs
                )
                if resp.status_code == 401:
                    log.info("Session expired — re-authenticating...")
                    self._authenticate()
                    resp = requests.request(
                        method, url, headers=self._headers(), timeout=10, **kwargs
                    )
                resp.raise_for_status()
                return resp.json() if resp.content else {}
            except Exception as e:
                if attempt == 0:
                    log.warning(f"API call failed (attempt 1), retrying in 2s: {e}")
                    time.sleep(2)
                else:
                    log.error(f"API call failed after retry: {e}")
                    raise

    def get_positions(self, epic=None):
        data      = self._request("GET", "/api/v1/positions")
        positions = []
        for item in data.get("positions", []):
            pos    = item.get("position", {})
            market = item.get("market", {})
            if epic is None or market.get("epic") == epic:
                positions.append({
                    "dealId":    pos.get("dealId"),
                    "direction": pos.get("direction"),
                    "size":      pos.get("size"),
                    "level":     pos.get("level"),
                    "epic":      market.get("epic")
                })
        log.info(f"Open positions for {epic or 'all'}: {len(positions)}")
        return positions

    def open_position(self, epic, direction, size):
        body = {
            "epic":           epic,
            "direction":      direction,
            "size":           size,
            "guaranteedStop": False
        }
        log.info(f"Opening {direction} {size} x {epic}")
        result = self._request("POST", "/api/v1/positions", json=body)
        log.info(f"Open position result: {result}")
        return result

    def close_position(self, deal_id):
        log.info(f"Closing position {deal_id}")
        result = self._request("DELETE", f"/api/v1/positions/{deal_id}")
        log.info(f"Close position result: {result}")
        return result
