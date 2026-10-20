-- AutoCombatLog — включает запись боевого журнала сам, чтобы не вводить /combatlog
--
-- Зачем: команда /combatlog меняет настройку только до выхода из игры — клиент её
-- не сохраняет (в WTF\Config.wtf попадает advancedCombatLogging, а combatLogging
-- нет). Поэтому после каждого запуска игры запись приходится включать заново.
--
-- Что делает аддон: при входе в игру и при загрузке мира проверяет состояние
-- записи и, если она выключена, включает её тем же способом, что и /combatlog
-- (функция LoggingCombat). Заодно выставляет advancedCombatLogging.
--
-- Команды в чате:
--   /acl       — показать состояние записи
--   /acl on    — включить автозапись (по умолчанию включена)
--   /acl off   — выключить автозапись
--   /acl now   — включить запись прямо сейчас
--   /acl loot  — сколько записей о добыче собрано

local ADDON = "AutoCombatLog"

-- LoggingCombat ограничен: не больше 5 вызовов за 10 секунд на весь клиент
-- (общий предел с /combatlog и другими аддонами), поэтому обращаемся к нему
-- как можно реже.
local PAUSE_AFTER_CHECK = 10   -- пауза, если состояние записи неизвестно
local PAUSE_RECHECK = 60       -- перепроверка, когда думаем, что запись идёт
local RETRY_DELAY = 4          -- пауза перед повтором при упоре в лимит
local MAX_RETRIES = 5

local frame = CreateFrame("Frame")

local db = { enabled = true, advanced = true }
local knownOn = false    -- считаем, что запись включена (тогда API не дёргаем)
local asking = false     -- проверка уже выполняется
local lastCheck = 0      -- время последней проверки
local retries = 0

local function msg(text, r, g, b)
    DEFAULT_CHAT_FRAME:AddMessage("|cff4f8cf7" .. ADDON .. ":|r " .. text,
        r or 0.92, g or 0.93, b or 0.94)
end

local function applyAdvanced()
    if not db.advanced then return end
    pcall(SetCVar, "advancedCombatLogging", "1")
end

local warnedNoApi = false

-- На всякий случай: если клиент не умеет LoggingCombat (не должен, BfA 8.3 умеет)
local function apiOk()
    if type(LoggingCombat) == "function" then return true end
    if not warnedNoApi then
        warnedNoApi = true
        msg("этот клиент не поддерживает автоматическое включение записи — " ..
            "включи /combatlog вручную.", 1, 0.45, 0.45)
    end
    return false
end

local check  -- объявляем заранее: enableNow и check ссылаются друг на друга

local function enableNow(announce)
    if not apiOk() then
        asking = false
        return
    end
    local state = LoggingCombat()   -- запрос состояния
    if state == nil then
        -- упёрлись в ограничение вызовов — попробуем чуть позже
        asking = false
        if retries < MAX_RETRIES then
            retries = retries + 1
            C_Timer.After(RETRY_DELAY, function() check("retry") end)
        end
        return
    end

    if state then
        knownOn = true
        asking = false
        retries = 0
        if announce then msg("запись боевого журнала уже включена.") end
        return
    end

    local ok = LoggingCombat(true)  -- включаем
    asking = false
    if ok then
        knownOn = true
        retries = 0
        applyAdvanced()
        msg("запись боевого журнала включена — /combatlog больше не нужен.")
    elseif ok == nil then
        if retries < MAX_RETRIES then
            retries = retries + 1
            C_Timer.After(RETRY_DELAY, function() check("retry") end)
        end
    else
        knownOn = false
        msg("не удалось включить запись боевого журнала — включи /combatlog вручную.",
            1, 0.45, 0.45)
    end
end

-- reason: "login", "world", "retry", "force"
check = function(reason, announce)
    if not db.enabled and reason ~= "force" then return end
    if asking then return end
    if reason == "retry" then
        -- повторы по таймеру идут без паузы
    elseif knownOn then
        -- мы уверены, что запись идёт: изредка перепроверяем, не выключилась ли
        if GetTime() - lastCheck < PAUSE_RECHECK then return end
    elseif GetTime() - lastCheck < PAUSE_AFTER_CHECK then
        return
    end
    lastCheck = GetTime()
    asking = true
    enableNow(announce)
end

-- ---------------------------------------------------------------------------
-- Добыча
-- ---------------------------------------------------------------------------
-- В боевой журнал добыча не попадает, поэтому запоминаем её из чата:
-- клиент присылает сообщение вида
--   "Имя получает предмет: |cffa335ee|Hitem:174123::::::::60:250:::::|h[Название]|h|r"
-- Приложение прочитает AutoCombatLogDB.loot и сопоставит записи с заходами
-- по времени и подземелью.
local MAX_LOOT = 400

local function rememberLoot(text, player)
    if type(text) ~= "string" then return end
    local link = text:match("|c%x%x%x%x%x%x%x%x|Hitem:[^|]+|h%[[^%]]*%]|h")
        or text:match("|Hitem:[^|]+|h%[[^%]]*%]|h")
    if not link then return end
    local id = tonumber(link:match("item:(%d+)"))
    if not id then return end
    local name = link:match("|h%[([^%]]*)%]") or ""
    local _, _, quality, baseIlvl = GetItemInfo(id)
    local ilvl = baseIlvl
    if GetDetailedItemLevelInfo then
        local ok, detailed = pcall(GetDetailedItemLevelInfo, link)
        if ok and type(detailed) == "number" and detailed > 0 then ilvl = detailed end
    end
    db.loot = db.loot or {}
    local list = db.loot
    local now = time()
    -- одно и то же сообщение иногда приходит дважды — не дублируем
    local last = list[#list]
    if last and last.id == id and last.p == (player or "") and (now - last.t) <= 3 then
        return
    end
    list[#list + 1] = {
        t = now,
        p = player or "",
        id = id,
        n = name,
        q = quality or 3,
        ilvl = ilvl or 0,
        z = (GetZoneText and GetZoneText()) or "",
        i = select(8, GetInstanceInfo()) or 0,
    }
    while #list > MAX_LOOT do table.remove(list, 1) end
end

-- ---------------------------------------------------------------------------
-- Рейтинг Mythic+ (Fio / Raider.IO): ищем, откуда клиент берёт цифру
-- ---------------------------------------------------------------------------
local MAX_PROBE = 400
local PROBE_WORDS = { "fio", "score", "rating", "raiderio", "mythic" }
local PROBE_NAMESPACES = {
    "C_ChallengeMode", "C_MythicPlus", "C_PlayerInfo", "C_LFGList",
    "C_PartyInfo", "C_AddOns", "C_ChatInfo",
}

local function probeKey(name)
    local l = name:lower()
    for _, word in ipairs(PROBE_WORDS) do
        if l:find(word, 1, true) then return true end
    end
    return false
end

-- вызываем только явно читающие функции, чтобы ничего не натворить в игре
local function probeReadOnly(name)
    local l = name:lower()
    return l:find("^get") or l:find("^is") or l:find("^has") or l:find("^can")
end

local function probeText(value, limit)
    local t = type(value)
    if t == "number" or t == "string" or t == "boolean" then
        return tostring(value)
    end
    if t == "table" then
        local out, cap = {}, limit or 12
        for k2, v2 in pairs(value) do
            if #out >= cap then break end
            local t2 = type(v2)
            if t2 == "number" or t2 == "string" or t2 == "boolean" then
                out[#out + 1] = tostring(k2) .. "=" .. tostring(v2)
            elseif t2 == "table" then
                out[#out + 1] = tostring(k2) .. "=table"
            end
        end
        return table.concat(out, "; ")
    end
    return ""
end

-- текст, который фрейм реально показывает (FontString во вложенных регионах)
local function probeFrameTexts(obj, depth, out, path)
    if depth > 6 or #out >= 60 then return end
    local regions = { pcall(obj.GetRegions, obj) }
    if regions[1] then
        for i = 2, #regions do
            local region = regions[i]
            if region then
                local ok, isText = pcall(region.IsObjectType, region, "FontString")
                if ok and isText then
                    local ok2, txt = pcall(region.GetText, region)
                    if ok2 and type(txt) == "string" and txt ~= "" then
                        out[#out + 1] = path .. ": " .. txt
                    end
                end
            end
        end
    end
    local children = { pcall(obj.GetChildren, obj) }
    if children[1] then
        for i = 2, #children do
            local child = children[i]
            if child and #out < 60 then
                local ok, name = pcall(child.GetName, child)
                local label = (ok and name) and name or ("#" .. (i - 1))
                probeFrameTexts(child, depth + 1, out, path .. "/" .. tostring(label))
            end
        end
    end
end

-- рекурсивный дамп таблицы (MythicScore.data и т.п.): только простые значения
local function dumpTree(value, depth, out, path)
    if #out >= 400 or depth > 5 then return end
    local t = type(value)
    if t == "number" or t == "string" or t == "boolean" then
        out[#out + 1] = path .. " = " .. tostring(value)
        return
    end
    if t ~= "table" then return end
    if type(value.GetObjectType) == "function" or type(value.GetName) == "function" then
        local ok, name = pcall(value.GetName, value)
        out[#out + 1] = path .. " = <объект " .. tostring(ok and name or "?") .. ">"
        return
    end
    local n = 0
    for k, v in pairs(value) do
        n = n + 1
        if n > 40 then
            out[#out + 1] = path .. " ... (обрезано)"
            break
        end
        if type(v) ~= "function" then
            dumpTree(v, depth + 1, out, path .. "." .. tostring(k))
        end
    end
end

local function probeScore()
    if not db then return end
    db.probe = db.probe or {}
    db.probe.t = time()
    db.probe.globals = {}
    db.probe.who = tostring(UnitName("player")) .. " (" .. tostring(GetRealmName()) .. ")"
    local list = db.probe.globals
    local function add(name, value, full)
        if #list >= MAX_PROBE then return end
        local t = type(value)
        local text
        if t == "function" then
            if not probeReadOnly(name) then
                text = "функция"
            else
                local ok, r1, r2, r3 = pcall(value)
                if not ok then
                    text = "error: " .. tostring(r1)
                else
                    local parts = {}
                    for _, r in ipairs({ r1, r2, r3 }) do
                        local txt = probeText(r)
                        if txt ~= "" then parts[#parts + 1] = txt end
                    end
                    text = table.concat(parts, " | ")
                end
            end
        elseif t == "table" then
            local ok, isFrame = pcall(value.IsObjectType, value, "Frame")
            if ok and isFrame then
                local texts = {}
                probeFrameTexts(value, 0, texts, name)
                text = table.concat(texts, " || ")
                if text == "" then text = "фрейм без текста" end
            else
                text = probeText(value, full and 40 or 12)
            end
        else
            text = probeText(value)
        end
        list[#list + 1] = name .. " [" .. t .. "] = " .. tostring(text)
    end

    -- 1) самодельные объекты сервера/клиента: CU_* и всё про MythicScore
    local names = {}
    for name in pairs(_G) do
        if type(name) == "string" then
            local l = name:lower()
            if l:sub(1, 3) == "cu_" or l:find("mythicscore", 1, true) then
                names[#names + 1] = name
            end
        end
    end
    table.sort(names)
    for _, name in ipairs(names) do add(name, _G[name], true) end

    -- 2) прочие «рейтинговые» глобалы (только таблицы и функции, без строк локализации)
    for name, value in pairs(_G) do
        local t = type(value)
        if type(name) == "string" and probeKey(name) and (t == "table" or t == "function") then
            add(name, value)
        end
    end

    -- 3) известные пространства имён: смотрим все поля, а не только имена с ключевыми словами
    for _, ns in ipairs(PROBE_NAMESPACES) do
        local tbl = _G[ns]
        if type(tbl) == "table" then
            for k, v in pairs(tbl) do
                if type(k) == "string" and probeKey(k) then
                    add(ns .. "." .. k, v)
                end
            end
        end
    end

    db.probe.count = #list

    -- 4) внутренности серверной панели MythicScore: там лежит сам рейтинг
    db.probe.mythic = {}
    if type(_G.MythicScore) == "table" then
        dumpTree(_G.MythicScore, 0, db.probe.mythic, "MythicScore")
    end
    if type(_G.MythicScoreDB) == "table" then
        dumpTree(_G.MythicScoreDB, 0, db.probe.mythic, "MythicScoreDB")
    end
end

-- ---------------------------------------------------------------------------
-- Рейтинг Mythic+ (Fio): серверная панель MythicScore держит его в
-- MythicScore.data.overall — оттуда и берём цифру для приложения
-- ---------------------------------------------------------------------------
local function readMythicScore()
    if not db then return end
    local panel = _G.MythicScore
    local data = panel and panel.data
    if type(data) ~= "table" then return end
    local overall = data.overall
    local value = overall
    if type(overall) == "table" then
        value = overall[0] or overall[1]
    end
    if type(value) ~= "number" then return end
    db.score = value
    db.score_source = "MythicScore"
    db.score_t = time()
    if type(data.seasonId) == "number" then db.score_season = data.seasonId end
    -- лучшее по каждому подземелью (ключ — ChallengeModeID)
    local dungeons = {}
    if type(data.dungeons) == "table" then
        for id, entry in pairs(data.dungeons) do
            if type(entry) == "table" then
                local side = entry[0] or entry[1]
                if type(side) == "table" and type(side.best) == "number" then
                    dungeons[#dungeons + 1] = {
                        id = tonumber(id) or 0,
                        score = side.best,
                        map = tonumber(entry.mapId) or 0,
                    }
                end
            end
        end
    end
    db.score_dungeons = dungeons
end

frame:SetScript("OnEvent", function(_, event, arg1, arg2, _, arg4)
    if event == "ADDON_LOADED" and arg1 == ADDON then
        AutoCombatLogDB = AutoCombatLogDB or {}
        if AutoCombatLogDB.enabled == nil then AutoCombatLogDB.enabled = true end
        if AutoCombatLogDB.advanced == nil then AutoCombatLogDB.advanced = true end
        db = AutoCombatLogDB
        -- раз в минуту подхватываем рейтинг, когда панель MythicScore его получит
        if C_Timer and C_Timer.NewTicker then C_Timer.NewTicker(60, readMythicScore) end
    elseif event == "PLAYER_LOGIN" then
        check("login", true)
    elseif event == "PLAYER_ENTERING_WORLD" then
        check("world", false)
        -- рейтинг Mythic+ (Fio) сервер отдаёт не сразу — читаем панель MythicScore
        if C_Timer and C_Timer.After then C_Timer.After(10, readMythicScore) end
    elseif event == "CHAT_MSG_LOOT" then
        rememberLoot(arg1, arg2)
    end
end)
frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("PLAYER_ENTERING_WORLD")
frame:RegisterEvent("CHAT_MSG_LOOT")

local function status()
    if not apiOk() then return end
    local state = LoggingCombat()
    local text
    if state == nil then
        text = "состояние неизвестно (лимит вызовов, спроси снова через 10 секунд)"
    elseif state then
        knownOn = true
        text = "запись боевого журнала включена"
    else
        knownOn = false
        text = "запись боевого журнала выключена"
    end
    local adv = GetCVar("advancedCombatLogging") == "1"
    msg(text .. "; расширенное логирование: " .. (adv and "вкл" or "выкл")
        .. "; автозапись: " .. (db.enabled and "вкл" or "выкл")
        .. "; добыча: " .. #(db.loot or {}) .. " записей"
        .. "; рейтинг: " .. (db.score and string.format("%.1f", db.score) or "—"))
end

SLASH_AUTOCOMBATLOG1 = "/acl"
SLASH_AUTOCOMBATLOG2 = "/autocombatlog"
SlashCmdList["AUTOCOMBATLOG"] = function(input)
    input = (input or ""):lower():gsub("%s+", "")
    if input == "on" or input == "off" then
        db.enabled = (input == "on")
        msg("автозапись " .. (db.enabled and "включена" or "выключена") .. ".")
        if db.enabled then
            knownOn = false
            check("force", true)
        end
    elseif input == "now" or input == "force" then
        knownOn = false
        check("force", true)
    elseif input == "loot" then
        local list = db.loot or {}
        msg("добыча: " .. #list .. " записей; последняя — "
            .. (list[#list] and (list[#list].n .. " (" .. (list[#list].p or "?") .. ")")
                or "нет"))
    elseif input == "probe" then
        readMythicScore()
        probeScore()
        local probe = db.probe or {}
        msg("поиск рейтинга: " .. tostring(probe.count or #(probe.globals or {}))
            .. " записей; рейтинг: " .. (db.score and tostring(db.score) or "не найден")
            .. ". Сделай /reload, чтобы сохранить.")
    elseif input == "score" then
        readMythicScore()
        msg("рейтинг Mythic+ (Fio): "
            .. (db.score and string.format("%.1f", db.score) or "не найден")
            .. (db.score_season and (" (сезон " .. tostring(db.score_season) .. ")") or ""))
    else
        status()
    end
end
