const messages = {
  NETWORK_ERROR:
    "Нет соединения с сервером. Проверьте подключение и повторите запрос.",
  TIMEOUT:
    "Сервер не ответил вовремя. Проверьте результат операции перед повторной отправкой.",
  INVALID_RESPONSE:
    "Ответ сервера не соответствует общему контракту. Передайте проблему интегратору.",
  VALIDATION_ERROR: "Сервер отклонил данные. Проверьте поля и формат файла.",
  VERSION_CONFLICT:
    "Появилась новая версия. Ваши изменения сохранены в форме. Загрузите актуальную версию для сравнения.",
  STATE_CONFLICT:
    "Данные уже изменились. Обновите состояние перед повторной отправкой.",
  NOT_FOUND:
    "Ресурс не найден. Возможно, ссылка устарела или маршрут ещё не подключён.",
  DATASET_UNAVAILABLE:
    "Источник наборов не настроен на сервере. Используйте загрузку JSON или обратитесь к интегратору.",
  DATASET_ERROR:
    "Не удалось получить набор. Проверьте группу и повторите позже.",
  DATABASE_UNAVAILABLE: "База данных временно недоступна. Повторите позже.",
  INTERNAL_ERROR:
    "Операция не выполнена из-за ошибки сервера. Повторите позже.",
  HTTP_ERROR: "Запрос не выполнен. Проверьте доступность API.",
  INVALID_JSON:
    "Нужен JSON с records, массивом записей или оболочкой data.records. Проверьте файл.",
  FILE_TOO_LARGE: "Файл слишком большой. Максимум — 10 МБ.",
  FORM_INVALID: "Проверьте отмеченные поля.",
  UI_ERROR: "Не удалось показать экран. Перезагрузите страницу.",
} as const;
export type ErrorCode = keyof typeof messages;
export type Event =
  | "load_failed"
  | "import_started"
  | "import_completed"
  | "import_failed"
  | "save_started"
  | "save_completed"
  | "save_failed"
  | "file_rejected"
  | "form_rejected"
  | "ui_failed";
export function logEvent(event: Event, status = 0, code?: ErrorCode) {
  const line = `cqa.ui event=${event} status=${status}${code ? ` error=${code}` : ""}`;
  if (code)
    (code === "INVALID_RESPONSE" ||
      status >= 500 ||
      (status === 0 &&
        !["INVALID_JSON", "FILE_TOO_LARGE", "FORM_INVALID"].includes(code))
      ? console.error
      : console.warn)(line);
  else console.info(line);
}
export class ApiError extends Error {
  constructor(
    public code: ErrorCode,
    public status = 0,
  ) {
    super(messages[code]);
  }
}
export function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : messages.UI_ERROR;
}
export function safeCode(raw: unknown, status: number): ErrorCode {
  if (typeof raw === "string" && Object.hasOwn(messages, raw))
    return raw as ErrorCode;
  return status === 404
    ? "NOT_FOUND"
    : status === 409
      ? "STATE_CONFLICT"
      : status === 422
        ? "VALIDATION_ERROR"
        : status >= 500
          ? "INTERNAL_ERROR"
          : "HTTP_ERROR";
}
