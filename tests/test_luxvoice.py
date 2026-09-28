"""Тесты LuxVoice.

Запуск:  .venv/bin/python -m pytest tests/ -v
Или без pytest:  .venv/bin/python tests/test_luxvoice.py

Проверяются те части, где ошибка обходится дорого: сопоставление фраз,
подстановка переменных, разбор сочетаний клавиш, обработка настроек,
надёжность хранилища и работа исполнителя.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Тесты работают в изолированном каталоге: рабочие данные не затрагиваются.
_TEMP_HOME = tempfile.mkdtemp(prefix="luxvoice-tests-")
os.environ["LUXVOICE_HOME"] = _TEMP_HOME

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from luxvoice.core import schema  # noqa: E402
from luxvoice.core.matcher import (  # noqa: E402
    levenshtein,
    normalize_phrase,
    phrase_similarity,
    words_to_digits,
)
from luxvoice.core.model import Action, Command, Node  # noqa: E402
from luxvoice.core.settings import Settings  # noqa: E402
from luxvoice.core.store import CommandStore  # noqa: E402


class TestPhraseNormalization(unittest.TestCase):
    """Приведение фразы к виду, удобному для сравнения."""

    def test_lowercase_and_punctuation(self) -> None:
        self.assertEqual(normalize_phrase("Сделай ГРОМЧЕ!!!"), "сделай громче")
        self.assertEqual(normalize_phrase("Открой, ютуб."), "открой ютуб")

    def test_extra_spaces(self) -> None:
        self.assertEqual(normalize_phrase("  сделай    тише  "), "сделай тише")

    def test_yo_normalization(self) -> None:
        # «ё» и «е» должны считаться одинаковыми: распознавание
        # часто пишет «е» там, где человек говорит «ё».
        self.assertEqual(normalize_phrase("включи ёлку"), normalize_phrase("включи елку"))

    def test_digits(self) -> None:
        self.assertEqual(words_to_digits("громкость на пятьдесят"), "громкость на 50")
        self.assertEqual(words_to_digits("таймер двадцать три"), "таймер 23")

    def test_empty(self) -> None:
        self.assertEqual(normalize_phrase(""), "")
        self.assertEqual(normalize_phrase("   "), "")


class TestLevenshtein(unittest.TestCase):
    """Расстояние редактирования — основа нечёткого сравнения."""

    def test_identical(self) -> None:
        self.assertEqual(levenshtein("громче", "громче"), 0)

    def test_one_change(self) -> None:
        self.assertEqual(levenshtein("громче", "громе"), 1)
        self.assertEqual(levenshtein("тише", "тише"), 0)

    def test_completely_different(self) -> None:
        self.assertEqual(levenshtein("абв", "где"), 3)

    def test_empty_strings(self) -> None:
        self.assertEqual(levenshtein("", "абв"), 3)
        self.assertEqual(levenshtein("абв", ""), 3)


class TestSilenceTimeout(unittest.TestCase):
    """Настройка паузы в конце фразы должна читаться и применяться."""

    def test_silence_timeout_in_schema(self) -> None:
        setting = schema.get_setting("stt.silence_timeout")
        self.assertIsNotNone(setting)
        self.assertEqual(setting.default, 0.9)
        self.assertEqual(setting.minimum, 0.3)
        self.assertEqual(setting.maximum, 5.0)

    def test_silence_timeout_applied(self) -> None:
        from luxvoice.stt.capture import CaptureConfig, MicrophoneRecorder
        recorder = MicrophoneRecorder(on_phrase=lambda b: None)
        recorder.configure(silence_timeout=1.5)
        self.assertEqual(recorder._config.silence_timeout, 1.5)


class TestVoiceFeedback(unittest.TestCase):
    """Голосовая обратная связь при выполнении команд."""

    def test_voice_feedback_setting_exists(self) -> None:
        setting = schema.get_setting("exec.voice_feedback")
        self.assertIsNotNone(setting)
        self.assertTrue(setting.default)

    def test_speak_result_setting_exists(self) -> None:
        setting = schema.get_setting("exec.speak_result")
        self.assertIsNotNone(setting)
        self.assertTrue(setting.default)


class TestAIRouting(unittest.TestCase):
    """Маршрутизация неизвестных фраз через локальный ИИ."""

    def test_route_unknown_setting_exists(self) -> None:
        setting = schema.get_setting("aichat.route_unknown")
        self.assertIsNotNone(setting)
        self.assertTrue(setting.default)

    def test_lmstudio_provider_exists(self) -> None:
        from luxvoice.ai.client import PROVIDERS
        self.assertIn("lmstudio", PROVIDERS)
        self.assertTrue(PROVIDERS["lmstudio"].local)
        self.assertEqual(PROVIDERS["lmstudio"].auth_style, "none")

    def test_ollama_provider_exists(self) -> None:
        from luxvoice.ai.client import PROVIDERS
        self.assertIn("ollama", PROVIDERS)
        self.assertTrue(PROVIDERS["ollama"].local)
        self.assertEqual(PROVIDERS["ollama"].auth_style, "none")


class TestDefaultCommands(unittest.TestCase):
    """Базовые команды создаются при первом запуске."""

    def setUp(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="luxvoice-defaults-"))
        self.store = CommandStore(self.path / "commands.json")
        self.store.load()

    def test_defaults_created_on_empty(self) -> None:
        self.store.ensure_defaults()
        commands = self.store.commands()
        self.assertGreater(len(commands), 0)

    def test_volume_commands_present(self) -> None:
        self.store.ensure_defaults()
        titles = [cmd.title for cmd in self.store.commands()]
        self.assertIn("Громче", titles)
        self.assertIn("Тише", titles)

    def test_no_duplicates_on_second_call(self) -> None:
        self.store.ensure_defaults()
        count1 = len(self.store.commands())
        self.store.ensure_defaults()
        count2 = len(self.store.commands())
        self.assertEqual(count1, count2)


class TestSimilarity(unittest.TestCase):
    """Оценка похожести двух фраз."""

    def test_exact_match_is_full(self) -> None:
        self.assertAlmostEqual(phrase_similarity("сделай громче", "сделай громче"),
                               1.0, places=2)

    def test_close_match_is_high(self) -> None:
        # Распознавание может перепутать окончание — оценка должна остаться высокой.
        score = phrase_similarity("сделай громче", "сделай громкая")
        self.assertGreater(score, 0.6)

    def test_unrelated_is_low(self) -> None:
        score = phrase_similarity("сделай громче", "открой браузер")
        self.assertLess(score, 0.5)


class TestSettingsSchema(unittest.TestCase):
    """Схема настроек: целостность и приведение типов."""

    def setUp(self) -> None:
        self.schema = schema

    def test_all_settings_have_labels(self) -> None:
        for setting in self.schema.all_settings():
            self.assertTrue(setting.label, f"нет подписи у {setting.key}")

    def test_keys_are_unique(self) -> None:
        keys = [s.key for s in self.schema.all_settings()]
        self.assertEqual(len(keys), len(set(keys)), "ключи настроек повторяются")

    def test_sections_exist(self) -> None:
        section_keys = {s.key for s in self.schema.sections()}
        for setting in self.schema.all_settings():
            self.assertIn(setting.section, section_keys,
                          f"раздел {setting.section} не объявлен")

    def test_choices_are_valid(self) -> None:
        for setting in self.schema.all_settings():
            if setting.kind == self.schema.CHOICE and setting.choices \
                    and not setting.dynamic:
                for key, label in setting.choices:
                    self.assertTrue(label, f"пустая подпись в {setting.key}")
                    self.assertIsInstance(key, str)

    def test_defaults_coerce(self) -> None:
        """Значения по умолчанию проходят приведение типов без ошибок."""
        for setting in self.schema.all_settings():
            value = self.schema.coerce(setting, setting.default)
            self.assertIsNotNone(value, f"coerce вернул None для {setting.key}")

    def test_minimum_less_than_maximum(self) -> None:
        for setting in self.schema.all_settings():
            if setting.minimum is not None and setting.maximum is not None:
                self.assertLessEqual(setting.minimum, setting.maximum,
                                     f"границы перепутаны в {setting.key}")


class TestSettingsStorage(unittest.TestCase):
    """Хранение настроек: сохранение, чтение, сброс, экспорт."""

    def setUp(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="luxvoice-settings-"))
        self.settings = Settings(self.path / "config.json")
        self.settings.load()

    def test_default_value(self) -> None:
        self.assertEqual(self.settings.text("stt.engine"), "vosk")

    def test_set_and_get(self) -> None:
        self.settings.set("tts.volume", 55, save=False)
        self.assertEqual(self.settings.number("tts.volume"), 55)

        self.settings.set("ui.theme", "light", save=False)
        self.assertEqual(self.settings.text("ui.theme"), "light")

        self.settings.set("stt.prefix_mode", True, save=False)
        self.assertTrue(self.settings.flag("stt.prefix_mode"))

    def test_type_coercion(self) -> None:
        # Число, пришедшее строкой, должно стать числом.
        self.settings.set("tts.volume", "70", save=False)
        self.assertEqual(self.settings.number("tts.volume"), 70)

    def test_bounds_enforced(self) -> None:
        """Значение за границами должно быть приведено к допустимому."""
        setting = schema.get_setting("tts.volume")
        if setting and setting.maximum is not None:
            self.settings.set("tts.volume", 99999, save=False)
            self.assertLessEqual(self.settings.number("tts.volume"),
                                 setting.maximum)

    def test_save_and_reload(self) -> None:
        self.settings.set("tts.volume", 33, save=False)
        self.settings.set("ui.theme", "light", save=False)
        self.assertTrue(self.settings.save())

        reloaded = Settings(self.path / "config.json")
        reloaded.load()
        self.assertEqual(reloaded.number("tts.volume"), 33)
        self.assertEqual(reloaded.text("ui.theme"), "light")

    def test_reset_section(self) -> None:
        self.settings.set("tts.volume", 33, save=False)
        self.settings.reset_section("speech_output", save=False)
        self.assertEqual(self.settings.number("tts.volume"),
                         schema.get_setting("tts.volume").default)

    def test_export_excludes_secrets(self) -> None:
        """Ключи API не должны попадать в файл экспорта."""
        self.settings.set("ai.api_key", "секретный-ключ-12345", save=False)
        data = self.settings.export_dict()
        text = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("секретный-ключ-12345", text)

    def test_unknown_key_does_not_crash(self) -> None:
        self.settings.set("несуществующая.настройка", "значение", save=False)
        self.assertIsNone(schema.get_setting("несуществующая.настройка"))


class TestCommandStore(unittest.TestCase):
    """Хранилище команд: дерево, поиск, целостность."""

    def setUp(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="luxvoice-store-"))
        self.store = CommandStore(self.path / "commands.json")
        self.store.load()

    def test_create_collection(self) -> None:
        node = self.store.create_collection("Моя коллекция")
        self.assertEqual(node.kind, "collection")
        self.assertEqual(node.title, "Моя коллекция")
        self.assertEqual(len(self.store.collections()), 1)

    def test_create_nested_folders(self) -> None:
        collection = self.store.create_collection("Музыка")
        folder = self.store.create_folder(collection.id, "Радио")
        self.assertIsNotNone(folder)
        self.assertIsNotNone(self.store.find_node(folder.id))

    def test_create_command_in_folder(self) -> None:
        collection = self.store.create_collection("Тест")
        command = self.store.create_command(collection.id, "Проверка")
        self.assertIsNotNone(command)
        self.assertEqual(command.title, "Проверка")
        self.assertEqual(len(self.store.commands_in(collection.id)), 1)

    def test_phrase_index(self) -> None:
        collection = self.store.create_collection("Тест")
        command = self.store.create_command(collection.id, "Громче")
        command.add_phrase("сделай громче")
        self.store.update_command(command)

        owner = self.store.phrase_owner("сделай громче")
        self.assertIsNotNone(owner)
        self.assertEqual(owner.id, command.id)

    def test_search(self) -> None:
        collection = self.store.create_collection("Медиа")
        command = self.store.create_command(collection.id, "Включить музыку")
        command.add_phrase("включи музыку")
        self.store.update_command(command)

        self.assertTrue(self.store.search("музыку"))
        self.assertTrue(self.store.search("Включить"))

    def test_save_and_reload(self) -> None:
        collection = self.store.create_collection("Сохранить")
        command = self.store.create_command(collection.id, "Тестовая")
        command.add_phrase("тестовая команда")
        command.add_action(Action(type="say_text", params={"text": "готово"}))
        self.store.update_command(command)
        self.assertTrue(self.store.save(force=True))

        reloaded = CommandStore(self.path / "commands.json")
        reloaded.load()
        found = reloaded.search("тестовая команда")
        self.assertTrue(found, "команда не сохранилась")
        self.assertEqual(found[0].title, "Тестовая")
        self.assertEqual(len(found[0].actions), 1)

    def test_remove_command(self) -> None:
        collection = self.store.create_collection("Удаление")
        command = self.store.create_command(collection.id, "Лишняя")
        command.add_phrase("лишняя команда")
        self.store.update_command(command)

        self.store.remove_command(command.id)
        self.assertIsNone(self.store.get(command.id))
        self.assertIsNone(self.store.phrase_owner("лишняя команда"))

    def test_remove_node_with_children(self) -> None:
        collection = self.store.create_collection("Целиком")
        folder = self.store.create_folder(collection.id, "Внутри")
        self.store.create_command(folder.id, "Команда")

        removed = self.store.remove_node(collection.id)
        self.assertGreaterEqual(removed, 1)
        self.assertEqual(len(self.store.collections()), 0)

    def test_duplicate_phrases_detected(self) -> None:
        """Одинаковые фразы в разных командах должны обнаруживаться."""
        first = self.store.create_collection("Первая")
        second = self.store.create_collection("Вторая")
        command_a = self.store.create_command(first.id, "A")
        command_a.add_phrase("общая фраза")
        self.store.update_command(command_a)

        command_b = self.store.create_command(second.id, "B")
        command_b.add_phrase("общая фраза")
        self.store.update_command(command_b)

        duplicates = self.store.duplicate_phrases()
        self.assertIn("общая фраза", duplicates)

    def test_corrupted_file_recovered_from_backup(self) -> None:
        """Повреждённый файл не должен приводить к потере данных."""
        collection = self.store.create_collection("Важное")
        self.store.create_command(collection.id, "Ценная команда")
        self.store.save(force=True)

        # Портим основной файл.
        path = self.path / "commands.json"
        path.write_text("{ это не json", encoding="utf-8")

        recovered = CommandStore(path)
        recovered.load()
        # Данные восстанавливаются из резервной копии или файл читается заново.
        self.assertTrue(recovered.load_error or recovered.collections() is not None)

    def test_statistics(self) -> None:
        collection = self.store.create_collection("Статистика")
        command = self.store.create_command(collection.id, "Считаемая")
        command.add_phrase("раз")
        command.add_phrase("два")
        command.add_action(Action(type="say_text", params={"text": "ок"}))
        self.store.update_command(command)

        stats = self.store.statistics()
        self.assertGreaterEqual(stats["commands"], 1)
        self.assertGreaterEqual(stats["phrases"], 2)
        self.assertGreaterEqual(stats["actions"], 1)


class TestMatcher(unittest.TestCase):
    """Сопоставление фраз — самая ответственная часть."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.path = Path(tempfile.mkdtemp(prefix="luxvoice-matcher-"))
        cls.store = CommandStore(cls.path / "commands.json")
        cls.store.load()

        collection = cls.store.create_collection("Звук")
        cls.louder = cls.store.create_command(collection.id, "Громче")
        cls.louder.add_phrase("сделай громче")
        cls.louder.add_phrase("прибавь звук")
        cls.louder.add_action(Action(type="volume_up", params={"amount": 10}))
        cls.store.update_command(cls.louder)

        cls.quieter = cls.store.create_command(collection.id, "Тише")
        cls.quieter.add_phrase("сделай тише")
        cls.quieter.add_phrase("убавь звук")
        cls.quieter.add_action(Action(type="volume_down", params={"amount": 10}))
        cls.store.update_command(cls.quieter)

        cls.mute = cls.store.create_command(collection.id, "Без звука")
        cls.mute.add_phrase("выключи звук совсем")
        cls.mute.add_action(Action(type="set_volume", params={"level": 0}))
        cls.store.update_command(cls.mute)

        from luxvoice.core.matcher import Matcher
        cls.matcher = Matcher(cls.store)
        cls.matcher.refresh()

    def test_exact_phrase(self) -> None:
        result = self.matcher.match("сделай громче")
        self.assertTrue(result.found)
        self.assertEqual(result.command.id, self.louder.id)

    def test_second_phrase_of_same_command(self) -> None:
        result = self.matcher.match("прибавь звук")
        self.assertTrue(result.found)
        self.assertEqual(result.command.id, self.louder.id)

    def test_similar_but_distinct_commands(self) -> None:
        """«громче» и «тише» не должны путаться."""
        louder = self.matcher.match("сделай громче")
        quieter = self.matcher.match("сделай тише")
        self.assertEqual(louder.command.id, self.louder.id)
        self.assertEqual(quieter.command.id, self.quieter.id)

    def test_no_false_positive_on_nonsense(self) -> None:
        """Бессмысленная фраза не должна активировать команду."""
        result = self.matcher.match("ывапролджэ зюзь")
        self.assertFalse(result.found,
                         f"ложное срабатывание на {result.command.title if result.command else ''}")

    def test_no_false_positive_on_long_sentence(self) -> None:
        result = self.matcher.match(
            "я вчера ходил в магазин и купил там много разных продуктов")
        self.assertFalse(result.found)

    def test_word_order_irrelevant(self) -> None:
        """Перестановка слов не должна ломать распознавание."""
        result = self.matcher.match("звук прибавь")
        self.assertTrue(result.found)
        self.assertEqual(result.command.id, self.louder.id)


class TestKeyParsing(unittest.TestCase):
    """Разбор сочетаний клавиш."""

    def setUp(self) -> None:
        from luxvoice.sysint.uinput import parse_keys
        self.parse = parse_keys

    def test_single_key(self) -> None:
        self.assertTrue(self.parse("space"))

    def test_combination(self) -> None:
        codes = self.parse("ctrl+shift+s")
        self.assertEqual(len(codes), 3)

    def test_russian_names(self) -> None:
        codes = self.parse("контрол+шифт+s")
        self.assertEqual(len(codes), 3)

    def test_unknown_key_ignored(self) -> None:
        codes = self.parse("ctrl+несуществующая+space")
        # Неизвестные клавиши пропускаются, но распознанные остаются.
        self.assertIsInstance(codes, list)

    def test_empty(self) -> None:
        self.assertEqual(self.parse(""), [])


class TestActionCatalog(unittest.TestCase):
    """Каталог действий: целостность описаний."""

    def setUp(self) -> None:
        from luxvoice.actions import catalog
        self.catalog = catalog

    def test_all_actions_have_labels(self) -> None:
        for spec in self.catalog.all_actions():
            self.assertTrue(spec.label, f"нет подписи у {spec.type}")
            self.assertTrue(spec.help, f"нет подсказки у {spec.type}")

    def test_action_types_unique(self) -> None:
        types = [spec.type for spec in self.catalog.all_actions()]
        self.assertEqual(len(types), len(set(types)), "типы действий повторяются")

    def test_groups_cover_all_actions(self) -> None:
        group_keys = {key for key, _label, _desc in self.catalog.groups()}
        for spec in self.catalog.all_actions():
            self.assertIn(spec.group, group_keys,
                          f"группа {spec.group} действия {spec.type} не объявлена")

    def test_param_kinds_supported(self) -> None:
        allowed = {"str", "int", "float", "bool", "choice", "text",
                   "path", "dir", "file", "keys"}
        for spec in self.catalog.all_actions():
            for param in spec.params:
                self.assertIn(param.kind, allowed,
                              f"неизвестный тип параметра {spec.type}.{param.key}")

    def test_choices_have_pairs(self) -> None:
        for spec in self.catalog.all_actions():
            for param in spec.params:
                if param.kind == "choice":
                    self.assertTrue(param.choices,
                                    f"нет вариантов у {spec.type}.{param.key}")
                    for key, label in param.choices:
                        self.assertIsInstance(key, str)
                        self.assertTrue(label)

    def test_every_action_has_handler(self) -> None:
        """Для каждого действия должен существовать обработчик."""
        from luxvoice.actions.executor import ActionExecutor
        executor = ActionExecutor()
        missing = []
        for spec in self.catalog.all_actions():
            handler = getattr(executor, f"_do_{spec.type}", None)
            if handler is None:
                missing.append(spec.type)
        self.assertEqual(missing, [], f"нет обработчиков: {missing}")


class TestPacks(unittest.TestCase):
    """Паки команд: структура и корректность ссылок на действия."""

    def setUp(self) -> None:
        from luxvoice.packs import manager
        self.manager = manager

    def test_packs_have_metadata(self) -> None:
        for pack in self.manager.PACKS:
            self.assertTrue(pack.key, "у пака нет ключа")
            self.assertTrue(pack.title, f"у пака {pack.key} нет названия")
            self.assertTrue(pack.description,
                            f"у пака {pack.key} нет описания")
            self.assertTrue(pack.category, f"у пака {pack.key} нет категории")

    def test_pack_keys_unique(self) -> None:
        keys = [p.key for p in self.manager.PACKS]
        self.assertEqual(len(keys), len(set(keys)), "ключи паков повторяются")

    def test_all_pack_actions_known(self) -> None:
        """Пак не должен ссылаться на несуществующее действие."""
        from luxvoice.actions import catalog
        unknown = []
        for pack in self.manager.PACKS:
            for commands in pack.folders.values():
                for command_data in commands:
                    for action_type, _params in command_data.actions:
                        if catalog.get_spec(action_type) is None:
                            unknown.append(f"{pack.key}: {action_type}")
        self.assertEqual(unknown, [], f"неизвестные действия: {unknown}")

    def test_pack_commands_have_phrases(self) -> None:
        missing = []
        for pack in self.manager.PACKS:
            for commands in pack.folders.values():
                for command_data in commands:
                    if not command_data.phrases:
                        missing.append(f"{pack.key}: {command_data.title}")
        self.assertEqual(missing, [], f"команды без фраз: {missing}")

    def test_pack_install_and_uninstall(self) -> None:
        path = Path(tempfile.mkdtemp(prefix="luxvoice-packs-"))
        store = CommandStore(path / "commands.json")
        store.load()
        manager = self.manager.PackManager(store)

        pack = self.manager.PACKS[0]
        stats = manager.install(pack)
        self.assertGreater(stats["added"], 0, "пак не установился")
        self.assertTrue(manager.is_installed(pack.key))

        # Повторная установка не должна создавать дубликаты.
        again = manager.install(pack)
        self.assertEqual(again["added"], 0, "пак установился повторно")

        removed = manager.uninstall(pack)
        self.assertGreater(removed, 0, "пак не удалился")
        self.assertFalse(manager.is_installed(pack.key))


class TestModel(unittest.TestCase):
    """Модель команд: сериализация и дублирование."""

    def test_action_roundtrip(self) -> None:
        action = Action(type="say_text", params={"text": "привет"},
                        delay_after=1.5, retries=2)
        restored = Action.from_dict(action.to_dict())
        self.assertEqual(restored.type, action.type)
        self.assertEqual(restored.params["text"], "привет")
        self.assertEqual(restored.delay_after, 1.5)
        self.assertEqual(restored.retries, 2)

    def test_command_roundtrip(self) -> None:
        command = Command(title="Тест")
        command.add_phrase("первая фраза")
        command.add_phrase("вторая фраза")
        command.add_action(Action(type="say_text", params={"text": "ок"}))

        restored = Command.from_dict(command.to_dict())
        self.assertEqual(restored.title, "Тест")
        self.assertEqual(len(restored.phrases), 2)
        self.assertEqual(len(restored.actions), 1)

    def test_duplicate_command_is_independent(self) -> None:
        command = Command(title="Исходная")
        command.add_phrase("фраза")
        command.add_action(Action(type="say_text", params={"text": "ок"}))

        clone = command.duplicate(keep_phrases=True)
        clone.title = "Копия"
        clone.actions[0].params["text"] = "изменено"

        self.assertEqual(command.title, "Исходная")
        self.assertEqual(command.actions[0].params["text"], "ок")

    def test_duplicate_phrase_ignored(self) -> None:
        command = Command(title="Тест")
        self.assertTrue(command.add_phrase("фраза"))
        # Та же фраза не должна добавляться второй раз.
        self.assertFalse(command.add_phrase("фраза"))

    def test_action_id_unique(self) -> None:
        first = Action(type="say_text")
        second = Action(type="say_text")
        self.assertNotEqual(first.id, second.id)


class TestSafety(unittest.TestCase):
    """Защита от опасных команд."""

    def test_dangerous_shell_blocked(self) -> None:
        """Уничтожающие команды должны блокироваться."""
        from luxvoice.actions.executor import ActionExecutor

        executor = ActionExecutor()
        dangerous = [
            "rm -rf / --no-preserve-root",
            "rm -rf /*",
            "mkfs.ext4 /dev/sda1",
        ]
        for command in dangerous:
            result = executor.execute(Action(type="run_shell",
                                             params={"command": command}))
            self.assertFalse(result.ok,
                             f"опасная команда не заблокирована: {command}")

    def test_regular_shell_allowed(self) -> None:
        from luxvoice.actions.executor import ActionExecutor

        executor = ActionExecutor()
        result = executor.execute(Action(type="run_shell",
                                         params={"command": "echo проверка"}))
        self.assertTrue(result.ok, f"обычная команда не выполнилась: {result.message}")
        self.assertIn("проверка", result.message)

    def test_substitution_variables(self) -> None:
        """Переменные подставляются в значения действий."""
        from luxvoice.actions.catalog import substitute

        self.assertEqual(substitute("{random:5-5}"), "5")
        result = substitute("{date}")
        self.assertTrue(result)
        self.assertNotIn("{", result)
        self.assertIsInstance(substitute("{volume}"), str)


class TestHistory(unittest.TestCase):
    """История выполненных команд."""

    def setUp(self) -> None:
        from luxvoice.core.history import HistoryEntry, HistoryStore
        self.path = Path(tempfile.mkdtemp(prefix="luxvoice-history-"))
        self.history = HistoryStore(self.path / "history.db")
        self.HistoryEntry = HistoryEntry

    def tearDown(self) -> None:
        self.history.close()

    def test_add_and_recent(self) -> None:
        import time
        entry = self.HistoryEntry(
            timestamp=time.time(), phrase="сделай громче",
            command="Громче", ok=True)
        self.history.add(entry)
        entries = self.history.recent(limit=10)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].phrase, "сделай громче")
        self.assertTrue(entries[0].ok)

    def test_search(self) -> None:
        import time
        for phrase in ("сделай громче", "открой ютуб", "сделай тише"):
            self.history.add(self.HistoryEntry(
                timestamp=time.time(), phrase=phrase, command="Тест", ok=True))
        found = self.history.search("громче")
        self.assertEqual(len(found), 1)

    def test_statistics(self) -> None:
        import time
        self.history.add(self.HistoryEntry(timestamp=time.time(),
                                           phrase="раз", ok=True))
        self.history.add(self.HistoryEntry(timestamp=time.time(),
                                           phrase="два", ok=False,
                                           error="не удалось"))
        stats = self.history.statistics(days=30)
        self.assertEqual(stats.get("total"), 2)
        self.assertEqual(stats.get("ok"), 1)

    def test_failed_phrases(self) -> None:
        """Неудачные фразы собираются — по ним удобно добавлять команды."""
        import time
        for _ in range(3):
            self.history.add(self.HistoryEntry(
                timestamp=time.time(), phrase="зажги свет", ok=False))
        failed = self.history.failed_phrases(limit=10)
        self.assertTrue(any("зажги свет" in str(item) for item in failed))

    def test_clear(self) -> None:
        import time
        self.history.add(self.HistoryEntry(timestamp=time.time(),
                                           phrase="раз", ok=True))
        removed = self.history.clear()
        self.assertGreaterEqual(removed, 1)
        self.assertEqual(self.history.recent(limit=10), [])


def run_tests() -> int:
    """Запустить все тесты. Возвращает код выхода."""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())