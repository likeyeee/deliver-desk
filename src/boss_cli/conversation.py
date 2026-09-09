"""Read only the verified conversation's rendered message bubbles."""

import hashlib
import json

MESSAGE_SCRIPT = r"""scope => {
  const visible = e => !!e.getClientRects().length && getComputedStyle(e).display !== 'none'
    && getComputedStyle(e).visibility !== 'hidden';
  return [...scope.querySelectorAll('.message-item')].filter(visible)
    .filter(row => !row.matches('.item-system, .system-message')).slice(-100).map(row => {
    const role = row.matches('.item-myself, .is-self, .is-me, .send') ? 'assistant' : 'user';
    const texts = [...row.querySelectorAll('.text-content, .text')].filter(visible)
      .filter(e => !e.querySelector('.text-content, .text'));
    const supported = texts.length === 1 && !row.matches('.item-system, .system-message')
      && !texts[0].querySelector('img, video, audio, a, button, .file-card');
    const text = supported ? texts[0].innerText.trim() : '';
    const usable = supported && !!text && text.length <= 4000;
    const content = usable ? text : '[非文字或过长消息，需到浏览器查看]';
    return {role, content, supported: usable,
      id: (row.getAttribute('data-message-id') || row.getAttribute('data-mid') || '').slice(0, 200)};
  });
}"""


def conversation(messages: list[dict], context_limit: int) -> dict:
    # Fingerprint a fixed window independent of model configuration. Unread counters,
    # receipt labels and the composer are deliberately excluded.
    digest = hashlib.sha256(
        json.dumps(messages[-50:], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    reason = ""
    if not messages:
        reason = "当前会话没有可读取的消息，请先在浏览器确认聊天记录已加载"
    elif messages[-1]["role"] != "user":
        reason = "最后一条消息是你发送的，等待招聘方回复后再生成"
    elif not messages[-1]["supported"]:
        reason = "最新消息不是可识别的纯文字，请到浏览器查看并手动处理"
    # A monitor must not treat loading older history as a new incoming message.
    # The latest inbound and its preceding own message identify the reply turn;
    # the broader fingerprint above still protects the actual generation/send.
    previous_own = next(
        (item for item in reversed(messages[:-1]) if item["role"] == "assistant"), None
    )
    reply_key = hashlib.sha256(
        json.dumps(
            [previous_own, messages[-1] if messages else None], ensure_ascii=False, sort_keys=True
        ).encode()
    ).hexdigest()
    return {
        "fingerprint": digest,
        "replyKey": reply_key,
        "messages": messages[-context_limit:],
        "canReply": not reason,
        "reason": reason,
    }
