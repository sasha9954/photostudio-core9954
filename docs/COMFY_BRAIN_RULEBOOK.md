# COMFY BRAIN RULEBOOK

## 1) MODE — главный закон драматургии

MODE задаёт **структуру истории**, а не только тон.

### clip
- Фокус: music-driven montage.
- Допускается ассоциативность и более мягкая причинность.
- Континуити: мягкое (по мотивам/образам).
- Сцены могут быть короче; приоритет audiovisual impact.

### kino
- Фокус: cinematic causality.
- Причинно-следственная цепочка должна быть явной.
- Континуити: сильное (герои, действия, пространство).
- Нельзя собирать историю как случайный набор клипов.

### reklama
- Фокус: commercial persuasion.
- Каждая сцена должна нести коммуникационную функцию.
- Product/message нельзя терять надолго.
- Логика: hook → value → payoff.

### scenario
- Фокус: structured storyboard.
- Самая строгая дисциплина текста и beat-by-beat логики.
- Музыка вторична по отношению к сценарию.
- Минимум монтажной абстракции.

---

## 2) AUDIO STORY MODE — policy источника сюжета

### lyrics_music
- Lyrics + music jointly drive story.
- Смысл слов можно использовать как narrative source.
- Музыка задаёт ритм, монтаж и эмоцию.

### music_only
- Lyrics semantics игнорируются.
- Сюжет строится по rhythm/energy/refs/mode.
- Без TEXT-node не имитируем буквальный «сюжет песни»:
  строим mood/energy progression.

### music_plus_text
- Lyrics semantics игнорируются.
- TEXT node — смысловой источник и направление сюжета.
- Музыка отвечает за тайминг, темп, эмоциональную динамику.

---

## 3) TEXT NODE — роль в управлении

Внутренняя шкала влияния TEXT:
- `override` — текст управляет сюжетом напрямую.
- `guide` — текст направляет, но не полностью диктует.
- `enhancer` — текст усиливает драматургию/акценты.
- `none` — текста нет или он не используется как источник.

Ожидаемое поведение:
- scenario: чаще `override`.
- kino: обычно `guide/override`.
- clip: `guide/enhancer` в зависимости от audio story mode.
- reklama: часто `override/guide` для message discipline.

---

## 4) STYLE — только визуальный фильтр

STYLE влияет на:
- visual treatment,
- light / color / finish,
- style continuity.

STYLE **не должен** ломать драматургию MODE.

Правило разделения:
- MODE decides **what story structure to build**.
- STYLE decides **how it should look**.

---

## 5) REFS — якоря идентичности и мира

REFS обеспечивают:
- стабильность персонажей/среды,
- визуальные anchor points,
- контроль world continuity.

Если refs недостаточно:
- brain может синтезировать сцены из text/audio/mode,
- но качество устойчивости персонажей и мира ниже.

---

## 6) Комбинации (что с чем комбинировать)

Рекомендуемые:
- scenario + music_plus_text + strong TEXT → строгий storyboard.
- kino + lyrics_music + refs персонажей/локации → причинный cinematic flow.
- reklama + music_plus_text + message в TEXT → контролируемая рекламная дуга.
- clip + music_only + сильные визуальные refs → выразительный монтаж.

Осторожно:
- scenario без TEXT: падает beat-by-beat дисциплина.
- music_plus_text без TEXT: lyrics игнорируются, остаётся music-only fallback.
- reklama без message в TEXT: риск потери коммуникационной функции.
- clip с слишком жёсткой причинностью: потеря монтажной свободы режима.

---

## 7) Ограничения по режимам

- clip: нельзя требовать жёсткой сценарной причинности на каждом шаге.
- kino: нельзя уводить сцены в случайную абстракцию.
- reklama: нельзя «забывать» продукт/message на длинных участках.
- scenario: нельзя подменять beat-by-beat логику чисто музыкальным монтажом.

---

## 8) Scene Contract Schema (v1)

Brain подготавливает scene-level contract поля (минимально в meta/mock):

- `sceneGoal`
- `storyBeat`
- `mustShow`
- `mustKeep`
- `cameraIntent`
- `transitionLogic`
- `renderPriority`
- `speechRelation`
- `abstractionLevel`

Это production-friendly каркас для будущего движка сцен.

---

## 9) Практика использования

1. Сначала выбрать MODE (структура истории).
2. Затем AUDIO STORY MODE (policy источника narrative).
3. Добавить TEXT при необходимости жёсткого управления смыслом.
4. Применить STYLE как визуальный слой.
5. Подключить REFS для identity/world continuity.

---

## 10) Где смотреть debug/meta

В planner/debug полезно проверять:
- `modeRules`
- `audioStoryPolicy`
- `textInfluence`
- `styleRules`
- `storyControlMode`
- `storyMissionSummary`
- `sceneContractSchemaVersion`

Если эти поля согласованы, brain работает rule-driven и предсказуемо.
