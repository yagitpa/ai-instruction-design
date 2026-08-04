# Исходный промпт

Публикуется как есть, без правок. Это рабочий запрос к кодовому агенту, а не учебный пример: он писался под реальную задачу и в целом справлялся с ней.

Задача: реализовать клиент к GigaChat API в Telegram-боте на Python и подключить его к обработчику сообщений.

---

Теперь реализуй gigachat_client.py по-настоящему. Заглушку удали.

ВАЖНО: используй ТОЛЬКО документацию ниже. Не опирайся на свои знания о GigaChat — они неверные. Если чего-то в документации нет — спроси меня, а не додумывай.

--- ДОКУМЕНТАЦИЯ GIGACHAT ---

Авторизация двухэтапная. Сначала по authorization key получаем временный access token, он живёт 30 минут. Затем этим токеном ходим в API.

Получение access token:

```python
import uuid
import requests

url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
headers = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
    "RqUID": str(uuid.uuid4()),
    "Authorization": f"Basic {GIGACHAT_AUTH_KEY}",
}
data = {"scope": GIGACHAT_SCOPE}
response = requests.post(url, headers=headers, data=data, timeout=30, verify=False)
response.raise_for_status()
access_token = response.json()["access_token"]
```

RqUID — уникальный идентификатор каждого запроса, обязательное требование API.
GIGACHAT_SCOPE для физлиц равен GIGACHAT_API_PERS.

Запрос к модели — через библиотеку openai как универсальный клиент:

```python
from openai import OpenAI
import httpx

client = OpenAI(
    api_key=access_token,
    base_url="https://gigachat.devices.sberbank.ru/api/v1",
    http_client=httpx.Client(verify=False),
)

completion = client.chat.completions.create(
    model="GigaChat",
    messages=[...],
    temperature=0.3,
)
answer = completion.choices[0].message.content
```

--- КОНЕЦ ДОКУМЕНТАЦИИ ---

Что нужно реализовать:

1. Кэширование токена. НЕ запрашивай токен на каждый вызов.
   Храни токен и время его получения в модуле.
   Считай токен протухшим через 25 минут (запас в 5 минут от заявленных 30).
   Обновляй только когда протух.

2. Функция ask(messages) -> str
   messages — список словарей вида {"role": ..., "content": ...}
   Возвращает текст ответа модели.
   temperature = 0.3, потому что бот-справочник, а не генератор идей.

3. Обработка ошибок.
   Если токен отвергнут (401) — один раз принудительно обнови токен и повтори запрос.
   Если и это не помогло — брось исключение с понятным текстом.
   Сетевые ошибки и таймауты логируй и брось исключение.
   Не глотай ошибки молча.

4. Очистка ответа от markdown.
   GigaChat часто оформляет ответ звёздочками и решётками, а также может обернуть его в тройные обратные кавычки.
   Напиши функцию clean_markdown(text), которая снимает обрамление тройными кавычками и убирает символы * # _ ` из текста.
   Применяй её к ответу перед возвратом.

5. Переменные окружения:
   GIGACHAT_AUTH_KEY
   GIGACHAT_SCOPE со значением по умолчанию GIGACHAT_API_PERS
   Обнови .env.example.

6. Над каждым местом с verify=False поставь комментарий:
   # ВНИМАНИЕ: отключена проверка SSL. Допустимо только для локальной разработки.
   # Перед выкаткой на сервер установить сертификаты НУЦ Минцифры.

7. Добавь в requirements.txt: openai, requests, httpx, urllib3, python-dotenv
   Предупреждения urllib3 об отключённом SSL погаси через urllib3.disable_warnings.

Теперь подключи это в main.py:
обработчик текстовых сообщений вместо эха собирает запрос из системного промпта, истории диалога и нового вопроса, вызывает ask() и отправляет ответ пользователю.
Перед вызовом ask() отправляй в чат действие "печатает".
Если ask() бросил исключение — пользователю уходит "Сервис временно недоступен, попробуй ещё раз", детали в лог.
