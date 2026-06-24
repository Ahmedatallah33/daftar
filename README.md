# تطبيق إدارة المعلم الأونلاين

تطبيق سطح مكتب Windows لإدارة حصص الطلاب، تتبع الدفعات كل 8 حصص، وتوليد فواتير PDF احترافية بالعربية.

## التثبيت

1. تأكد من تثبيت Python 3.11 أو أحدث.
2. افتح PowerShell أو CMD داخل مجلد المشروع وشغّل:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

3. خطوط Cairo العربية مرفقة داخل `app/resources/fonts/` وتُضمَّن في حزم PyInstaller.

## التشغيل

```bash
python main.py
```

- عند أول تشغيل سيُنشأ ملف `%LOCALAPPDATA%\TeacherHub\data\teacher.db` تلقائياً.
- افتح **إدارة الطلاب** من الشريط الجانبي لإضافة طلابك (الاسم، رابط Zoom، رقم واتساب، السعر، أيام الحصص، وقت الحصة).
- الفواتير تُحفظ في `%LOCALAPPDATA%\TeacherHub\exports\invoices\`.

## الميزات

- **📅 جدول اليوم**: يعرض طلاب اليوم الحاليين، وأزرار فتح Zoom، تسجيل حصة، واتساب.
- **👥 سجل الطلاب**: بطاقات لكل طالب مع عداد حصص وشريط تقدم، تتلون برتقالياً عند الوصول للحد (8 حصص).
- **💰 المستحقات والفواتير**: يعرض فقط الطلاب الذين تجاوزوا الحد، مع زر توليد PDF وزر تصفير العداد بعد التحصيل.
- **📊 التقارير الشهرية**: دخل الشهر، عدد الحصص، أكثر الطلاب نشاطاً.

## التحزيم (.exe مستقل)

شغّل الأوامر التالية من جذر المشروع.

ملف واحد:

```bash
pyinstaller --clean --noconfirm --onefile --windowed --name TeacherHub ^
  --add-data "app/resources;app/resources" ^
  --add-data "app/ui/styles;app/ui/styles" ^
  main.py
```

مجلد مستقل:

```bash
pyinstaller --clean --noconfirm --onedir --windowed --name TeacherHub ^
  --add-data "app/resources;app/resources" ^
  --add-data "app/ui/styles;app/ui/styles" ^
  main.py
```

البيانات القابلة للكتابة تبقى دائماً تحت `%LOCALAPPDATA%\TeacherHub\`، وليست
داخل مجلد التحزيم أو بجانب الملف التنفيذي.

## هيكل المشروع

- `main.py` — نقطة الدخول
- `app/config.py` — الإعدادات والمسارات
- `app/db/` — نماذج SQLAlchemy وإنشاء قاعدة البيانات
- `app/services/` — منطق الأعمال (طلاب، حصص، فواتير، PDF، روابط)
- `app/ui/` — واجهات المستخدم (نافذة رئيسية، صفحات، ودجتس، أنماط)
- `%LOCALAPPDATA%\TeacherHub\data\teacher.db` — قاعدة البيانات المحلية
- `%LOCALAPPDATA%\TeacherHub\backups\` — النسخ الاحتياطية المتحقّق من سلامتها
- `%LOCALAPPDATA%\TeacherHub\exports\` — صادرات Excel
- `%LOCALAPPDATA%\TeacherHub\exports\invoices\` — فواتير PDF المُصدَرة
- `%LOCALAPPDATA%\TeacherHub\logs\` — سجلات التشغيل والترحيل

## النسخ الاحتياطي والاستعادة

- النسخ الاحتياطية تحت `%LOCALAPPDATA%\TeacherHub\backups\` تحتوي قاعدة SQLite فقط؛
  ملفات PDF وXLSX المُصدَّرة ليست جزءاً من نسخة القاعدة.
- قبل الاستعادة اليدوية: أغلق التطبيق وكل عملياته، احتفظ بنسخة من قاعدة البيانات
  الحالية، تحقّق من سلامة النسخة المراد استعادتها، ولا تُعِد استخدام ملفات
  `teacher.db-wal` أو `teacher.db-shm` القديمة. بعد الاستعادة شغّل التطبيق وتحقّق من
  إجمالي الطلاب والحصص والفواتير والمدفوعات.
- يتتبّع `schema_meta` إصدار المخطط. الفواتير الحديثة ترتبط بالدورة عبر
  `cycle_signature`، أما الفاتورة القديمة غير المرتبطة فتظهر في مسار صريح باسم
  **«تسوية فاتورة قديمة»**.
