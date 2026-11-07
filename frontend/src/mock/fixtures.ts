// Authored synthetic data only. No organizer conversations or AI assessments.
import type { ChecklistVersion, OrganizerRecord } from "../api/schema";
export const demoRecords: OrganizerRecord[] = [
  {
    conversation_id: "SYN-RU-001",
    language: "RU",
    channel: "agent chat",
    audio_url: null,
    transcript: [
      {
        speaker: "system",
        timestamp: "00:00",
        text: "Синтетический пример для разработки интерфейса.",
      },
      {
        speaker: "client",
        timestamp: "00:01",
        text: "Здравствуйте! Где посмотреть историю операций?",
      },
      { speaker: "bot", timestamp: "00:02", text: "Соединяю с оператором." },
      {
        speaker: "operator",
        timestamp: "00:05",
        text: "Здравствуйте! Меня зовут Алия. Откройте раздел «История» в личном кабинете.",
      },
      { speaker: "client", timestamp: "00:18", text: "Нашёл, спасибо!" },
      {
        speaker: "operator",
        timestamp: "00:22",
        text: "Рада помочь. Если возникнут вопросы, напишите нам. До свидания!",
      },
    ],
  },
  {
    conversation_id: "SYN-KZ-002",
    language: "KZ",
    channel: "agent chat",
    audio_url: null,
    transcript: [
      {
        speaker: "client",
        timestamp: "01:10",
        text: "Сәлеметсіз бе! Құжаттарды қайдан табамын?",
      },
      {
        speaker: "operator",
        timestamp: "01:17",
        text: "Сәлеметсіз бе! Менің атым Айдана. Жеке кабинеттегі «Құжаттар» бөлімін ашыңыз.",
      },
    ],
  },
  {
    conversation_id: "SYN-EN-003",
    language: "EN",
    channel: "voice call",
    audio_url: "https://example.invalid/synthetic-audio.wav",
    transcript: [
      {
        speaker: "client",
        timestamp: "2026-11-07T10:00:01Z",
        text: "Hello, where can I find my account statement?",
      },
      {
        speaker: "operator",
        timestamp: "2026-11-07T10:00:05Z",
        text: "Hello! My name is Alex. Please open Documents in your account.",
      },
    ],
  },
];
export const demoChecklist: ChecklistVersion = {
  id: "20000000-0000-4000-8000-000000000001",
  checklist_id: "10000000-0000-4000-8000-000000000001",
  name: "Качество консультации",
  version_number: 1,
  created_at: "2026-11-07T10:00:00Z",
  criteria: [
    {
      id: "greeting_demo",
      name: "Начало разговора",
      instruction: "Оператор поздоровался и представился.",
      type: "binary",
      weight: 20,
      allow_na: false,
      na_rule: null,
      scale: null,
      section: "Общение",
    },
    {
      id: "clarity_demo",
      name: "Понятность ответа",
      instruction: "Оцените, насколько понятен следующий шаг для клиента.",
      type: "scale",
      weight: 60,
      allow_na: false,
      na_rule: null,
      section: "Консультация",
      scale: [
        { value: 0, description: "Следующий шаг не объяснён" },
        { value: 5, description: "Частично понятен" },
        { value: 10, description: "Понятен полностью" },
      ],
    },
    {
      id: "followup_demo",
      name: "Уточнение запроса",
      instruction: "Оператор уточнил недостающую информацию.",
      type: "binary",
      weight: 20,
      allow_na: true,
      na_rule: "Клиент сразу предоставил достаточно информации",
      scale: null,
      section: "Консультация",
    },
    {
      id: "risk_demo",
      name: "Нарушение",
      instruction: "Есть ли раскрытие конфиденциальной информации?",
      type: "flag",
      weight: 0,
      allow_na: false,
      na_rule: null,
      scale: null,
      section: null,
    },
  ],
};
