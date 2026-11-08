# Скачать и восстановить CQA Gemma v1

Веса и LoRA хранятся в [GitHub Release](https://github.com/abilkhayrbigali-design/HULK-S_FAMILY_REPO/releases/tag/cqa-gemma4-e4b-v1).
В Git включены только описание, лицензия и manifest; модельные бинарники не являются Git blobs/LFS.
Release — предварительный finetuned_candidate, не production-релиз приложения.

Для Linux/macOS/WSL с GitHub CLI:

```bash
gh release download cqa-gemma4-e4b-v1 --repo abilkhayrbigali-design/HULK-S_FAMILY_REPO --dir data/private/model-downloads/cqa-gemma4-e4b-v1
cd data/private/model-downloads/cqa-gemma4-e4b-v1
sha256sum --check SHA256SUMS
test ! -e cqa-gemma4-e4b-v1.tar
cat cqa-gemma4-e4b-v1.tar.part-001 cqa-gemma4-e4b-v1.tar.part-002 cqa-gemma4-e4b-v1.tar.part-003 > cqa-gemma4-e4b-v1.tar
sha256sum --check ARCHIVE_SHA256SUMS
test ! -e cqa-gemma4
tar --extract --file cqa-gemma4-e4b-v1.tar
```

Нужно около17GB свободного диска для частей, собранного архива и распаковки.
В macOS вместо `sha256sum` можно использовать `shasum -a 256`.
Проверки обязательны до распаковки. Отсутствующую/повреждённую часть скачайте заново;
сами файлы модели нельзя коммитить. Сборка/проверка выполняется в новом приватном каталоге.

После распаковки появятся GGUF, Modelfile, adapter/, лицензия, происхождение и технические отчёты.
Установить модель в запущенный Ollama:

```bash
cd cqa-gemma4
ollama create cqa-gemma4-e4b-rubric-v1 -f Modelfile
```

Для уже работающего на этом ноутбуке сервера добавьте `OLLAMA_HOST=127.0.0.1:11439`
перед командой Ollama. На другой машине используйте адрес своего локального сервера.

SHA256 архива: `bb3d40a9b3963f0830d9752c9318d0ae2ceb0e306d868412c0090c41baa5389a`.
SHA256 GGUF: `d6578ec5f98a0379148bdf1245286c5790ef7b56533376c1014690069e63f80a`.
Подробности/ограничения: [MODEL_CARD.md](MODEL_CARD.md).
Независимая GT-проверка и подключение к приложению ещё не завершены.
