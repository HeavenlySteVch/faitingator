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

frame:SetScript("OnEvent", function(_, event, arg1)
    if event == "ADDON_LOADED" and arg1 == ADDON then
        AutoCombatLogDB = AutoCombatLogDB or {}
        if AutoCombatLogDB.enabled == nil then AutoCombatLogDB.enabled = true end
        if AutoCombatLogDB.advanced == nil then AutoCombatLogDB.advanced = true end
        db = AutoCombatLogDB
    elseif event == "PLAYER_LOGIN" then
        check("login", true)
    elseif event == "PLAYER_ENTERING_WORLD" then
        check("world", false)
    end
end)
frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("PLAYER_ENTERING_WORLD")

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
        .. "; автозапись: " .. (db.enabled and "вкл" or "выкл"))
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
    else
        status()
    end
end
