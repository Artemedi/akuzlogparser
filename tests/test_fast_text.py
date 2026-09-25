"""Fast-path text scans preserve existing AKUZ classification semantics."""
import unittest
from akuz_log_parser import classify, normalize, extract_duration
from akuz_analytics import recognize_error


class FastTextTests(unittest.TestCase):
    def test_category_priority_not_first_match(self):
        cases=[
            ("no_data_found and timed out", "таймаут"),
            ("nack then an Exception", "ошибка/исключение"),
            ("не найдено, затем отказ в доступе","отказ/NACK"),
            ("text without any classification marker","прочее"),
            ("X"*64000+"ошибка", "ошибка/исключение"),
            ("отказано" + "X"*64000 + "TIMEOUT","таймаут"),
            ("FAİLED", "ошибка/исключение"),
            ("faılure","ошибка/исключение"),
        ]
        for raw,expected in cases:
            with self.subTest(label=raw[:40]):
                self.assertEqual(classify(raw),expected)

    def test_find_classifier_regex_semantics(self):
        # The optional whitespace in timed?\\s*out differs from the mandatory
        # whitespace in not\\s+found and истекло\\s+время\\s+ожидания.
        cases=[
            ("notfound", "прочее"),
            ("not found", "не найдено"),
            ("not"+chr(9)+"found", "не найдено"),
            ("не найден", "не найдено"),
            ("ненайден", "прочее"),
            ("истекловремяожидания", "прочее"),
            ("истекло время ожидания", "таймаут"),
            ("истекло"+chr(10)+"время"+chr(9)+"ожидания", "таймаут"),
            ("timedout", "таймаут"),
            ("time out", "таймаут"),
            ("timed out", "таймаут"),
            ("тайм"+chr(10)+"аут", "прочее"),
            ("TİMEOUT", "таймаут"),
            ("tımeout", "таймаут"),
            ("no_data_found", "не найдено"),
            # casefold expands ß into two chars; original тайм.?аут
            # considers it one wildcard char, so fast path must fall back.
            ("таймßаут", "таймаут"),
            ("таймßаут rejected", "таймаут"),
            ("таймßаут\nSystem.Exception", "таймаут"),
            ("notfoundİнайден", "прочее"),
            ("not\u00a0found", "не найдено"),
            ("\u0345timeout", "таймаут"),
            ("\u0345error", "ошибка/исключение"),
        ]
        for value, expected in cases:
            with self.subTest(message=repr(value)):
                self.assertEqual(classify(value), expected)

    def test_duration_priority_and_bounds(self):
        self.assertEqual(extract_duration("за 7 ms then общее время: 00:00:01.500"),
                         (1500.0,"общее время"))
        self.assertEqual(extract_duration("за 7 ms"),(7.0,"кэш"))
        self.assertIsNone(extract_duration("no time recorded"+"X"*64000))
        self.assertEqual(extract_duration("X"*64000+" за 3.25 ms"),(3.25,"кэш"))

    def test_normalization_uses_only_first_line(self):
        raw="X"*80000+"\nERROR: arbitrary"
        self.assertEqual(normalize(raw),"X"*330)
        self.assertEqual(normalize("first\n"+"X"*80000),"first")

    def test_error_detector_still_checks_first_line_or_first_24000(self):
        self.assertIsNone(recognize_error("X"*64000))
        self.assertIsNone(recognize_error("prefix\n"+"X"*24000+"Exception"))
        error=recognize_error("prefix\nSystem.MyException: synthetic")
        self.assertEqual(error["exception"],"MyException")
        self.assertEqual(error["family"],"Другие ошибки")
        self.assertEqual(recognize_error("first line timeout\n"+"X"*64000)["exception"],
                         "AKUZ error (text)")


if __name__=="__main__":
    unittest.main()
