"""company/systems/paypal.py —— 真·PayPal 收款适配器（Invoicing API v2）。

安全立场（§3 业务闸门 + 硬规矩「不划钱」）：agent 只「开发票 / 查状态」，
**绝不捕获资金 / 转账 / 划钱**。钱永远是客户在 PayPal 页面自己付、客户→公司，
本适配器不经手任何资金移动，也不实现 capture/payout 类接口。
  - create_payment_request → 建**草稿**发票（钱不动，还没发给客户）
  - send_invoice           → 把发票发给客户（对外承诺 = 人审后才调）
  - check_status           → 只读查是否 PAID（对账，不碰钱）

实现照 PayPal Invoicing v2 官方文档（已核对）：
  base   live=https://api-m.paypal.com  sandbox=https://api-m.sandbox.paypal.com
  OAuth  POST /v1/oauth2/token（Basic client_id:secret，grant_type=client_credentials）
  建票   POST /v2/invoicing/invoices（detail.currency_code / items[].unit_amount / primary_recipients[].billing_info.email_address）
  发送   POST /v2/invoicing/invoices/{id}/send → 202
  查询   GET  /v2/invoicing/invoices/{id} → status ∈ DRAFT/SCHEDULED/SENT/PAID/CANCELLED

⚠️金额与币种：本公司库里金额存 **CNY 分**。发票 value 按「self.currency 的小单位/100」
算——所以 currency 必须与金额单位一致。要用 USD 对海外客户结算，需先在上游把
CNY→USD 换算好（FX 汇率由你配置，本适配器**不猜汇率、不自动换算**，避免悄悄标错价）。

上线只差你（运行时输入，凭证绝不进代码/VM）：
  PAYPAL_CLIENT_ID / PAYPAL_SECRET（商家账号，可先用沙箱验）+ PAYPAL_CURRENCY。
"""

from __future__ import annotations

import base64
import json
import os
from typing import Callable, Optional

from company.roles.collections import PaymentAdapter  # 仅为类型对齐（Protocol）

LIVE_BASE = "https://api-m.paypal.com"
SANDBOX_BASE = "https://api-m.sandbox.paypal.com"

# transport 协议：(method, path, payload|None) -> 响应 dict。默认走真 urllib；测试注入假的。
Transport = Callable[[str, str, Optional[dict]], dict]


class PayPalError(RuntimeError):
    pass


def _id_from_href(href: str) -> str:
    return href.rstrip("/").rsplit("/", 1)[-1] if href else ""


class PayPalPaymentAdapter:
    """PayPal 收款适配器（Invoicing v2）。实现 PaymentAdapter 协议，可直插 CollectionAgent。"""

    requires_human_confirm = True  # 发发票=对外承诺、到账确认=只读，均不许 agent 擅自划钱

    def __init__(self, client_id: str = "", secret: str = "", *, sandbox: bool = True,
                 currency: str = "USD", invoicer_email: Optional[str] = None,
                 base_url: Optional[str] = None, transport: Optional[Transport] = None):
        self.client_id = client_id
        self.secret = secret
        self.base = base_url or (SANDBOX_BASE if sandbox else LIVE_BASE)
        self.currency = currency
        self.invoicer_email = invoicer_email
        self._transport = transport   # 注入即离线可测（跳过真网络/OAuth）
        self._token: Optional[str] = None

    # ------------------ 传输层 ------------------ #
    def _access_token(self) -> str:
        if self._token:
            return self._token
        import urllib.request
        creds = base64.b64encode(f"{self.client_id}:{self.secret}".encode()).decode()
        req = urllib.request.Request(
            self.base + "/v1/oauth2/token",
            data=b"grant_type=client_credentials", method="POST",
            headers={"Authorization": f"Basic {creds}",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310（固定 PayPal 域名）
            self._token = json.loads(r.read().decode() or "{}").get("access_token", "")
        if not self._token:
            raise PayPalError("拿不到 PayPal access_token（检查 client_id/secret/环境）")
        return self._token

    def _api(self, method: str, path: str, payload: Optional[dict] = None) -> dict:
        if self._transport is not None:            # 测试/离线：走注入的假传输
            return self._transport(method, path, payload)
        import urllib.request
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Authorization": f"Bearer {self._access_token()}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            body = r.read().decode() or ""
            return json.loads(body) if body else {}

    # ------------------ PaymentAdapter 协议：开草稿发票（钱不动）------------------ #
    def create_payment_request(self, amount: int, method: str, memo: str,
                               recipient_email: Optional[str] = None) -> dict:
        value = f"{amount / 100:.2f}"   # amount 是 self.currency 的小单位（分）；见文件头 ⚠️
        invoice: dict = {
            "detail": {"currency_code": self.currency, "note": memo},
            "items": [{"name": memo, "quantity": "1",
                       "unit_amount": {"currency_code": self.currency, "value": value}}],
        }
        if self.invoicer_email:
            invoice["invoicer"] = {"email_address": self.invoicer_email}
        if recipient_email:
            invoice["primary_recipients"] = [
                {"billing_info": {"email_address": recipient_email}}]

        created = self._api("POST", "/v2/invoicing/invoices", invoice)
        invoice_id = created.get("id") or _id_from_href(created.get("href", ""))
        return {
            "provider": "paypal",
            "provider_ref": invoice_id,
            "amount_yuan": amount / 100,
            "currency": self.currency,
            "method": "paypal",
            "memo": memo,
            "instruction": (f"PayPal 草稿发票已建 #{invoice_id}，金额 {value} {self.currency}。"
                            f"经人审后调 send_invoice() 才发给客户；客户在 PayPal 自行付款。"),
            "requires_human_confirm": True,
        }

    # ------------------ 对外发送（人审后才调）------------------ #
    def send_invoice(self, invoice_id: str) -> dict:
        """把发票发给客户 —— 对外承诺动作，须已过人审闸门再调。"""
        self._api("POST", f"/v2/invoicing/invoices/{invoice_id}/send",
                  {"send_to_recipient": True})
        return {"provider_ref": invoice_id, "sent": True}

    # ------------------ 只读对账（不碰钱）------------------ #
    def check_status(self, invoice_id: str) -> dict:
        """查发票状态（只读对账）。PAID/MARKED_AS_PAID = 客户已付。"""
        inv = self._api("GET", f"/v2/invoicing/invoices/{invoice_id}")
        status = inv.get("status", "UNKNOWN")
        due = inv.get("due_amount") or {}
        return {"provider_ref": invoice_id, "status": status,
                "paid": status in ("PAID", "MARKED_AS_PAID"),
                "due_value": due.get("value")}


def from_env(transport: Optional[Transport] = None) -> PayPalPaymentAdapter:
    """从环境变量构造（凭证不进代码）。PAYPAL_ENV=live 走生产，否则沙箱。"""
    return PayPalPaymentAdapter(
        client_id=os.environ.get("PAYPAL_CLIENT_ID", ""),
        secret=os.environ.get("PAYPAL_SECRET", ""),
        sandbox=os.environ.get("PAYPAL_ENV", "sandbox") != "live",
        currency=os.environ.get("PAYPAL_CURRENCY", "USD"),
        invoicer_email=os.environ.get("PAYPAL_INVOICER_EMAIL"),
        transport=transport,
    )
