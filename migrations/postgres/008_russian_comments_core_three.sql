-- =============================================================================
-- 008: русские COMMENT только для executor_runs, positions, trade_results
-- Безопасно: колонки из 005 комментируются только если существуют.
--
-- psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/postgres/008_russian_comments_core_three.sql
-- =============================================================================

CREATE OR REPLACE FUNCTION okx_exec._comment_column_if_exists(
    p_table TEXT,
    p_column TEXT,
    p_comment TEXT
) RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'okx_exec'
          AND table_name = p_table
          AND column_name = p_column
    ) THEN
        EXECUTE format(
            'COMMENT ON COLUMN okx_exec.%I.%I IS %L',
            p_table,
            p_column,
            p_comment
        );
    END IF;
END;
$$;

-- ---------------------------------------------------------------------------
-- executor_runs
-- ---------------------------------------------------------------------------
COMMENT ON TABLE okx_exec.executor_runs IS
    'Журнал запусков executor. Одна строка = один процесс (docker up → down). run_id связывает все торговые события. При очистке торговых таблиц эту таблицу обычно сохраняют.';

SELECT okx_exec._comment_column_if_exists('executor_runs', 'run_id', 'PK: номер запуска (автоинкремент). Используется во всех дочерних таблицах');
SELECT okx_exec._comment_column_if_exists('executor_runs', 'run_uuid', 'UUID запуска (глобально уникальный)');
SELECT okx_exec._comment_column_if_exists('executor_runs', 'started_at', 'UTC: момент старта процесса');
SELECT okx_exec._comment_column_if_exists('executor_runs', 'finished_at', 'UTC: момент остановки; NULL пока status=running');
SELECT okx_exec._comment_column_if_exists('executor_runs', 'runtime_mode', 'live | paper | replay (из OKX_HFT_RUNTIME_MODE)');
SELECT okx_exec._comment_column_if_exists('executor_runs', 'environment_name', 'Имя окружения (OKX_HFT_ENV)');
SELECT okx_exec._comment_column_if_exists('executor_runs', 'host_name', 'Hostname VPS / контейнера');
SELECT okx_exec._comment_column_if_exists('executor_runs', 'process_id', 'PID процесса Python внутри контейнера');
SELECT okx_exec._comment_column_if_exists('executor_runs', 'app_version', 'Версия пакета okx-hft-executor');
SELECT okx_exec._comment_column_if_exists('executor_runs', 'strategy_name', 'Основная стратегия run (например random_baseline_v1)');
SELECT okx_exec._comment_column_if_exists('executor_runs', 'model_name', 'Имя ML-модели; для baseline NULL');
SELECT okx_exec._comment_column_if_exists('executor_runs', 'inst_id', 'Инструмент запуска (например BTC-USDT-SWAP)');
SELECT okx_exec._comment_column_if_exists('executor_runs', 'status', 'running | stopped | error | draining');
SELECT okx_exec._comment_column_if_exists('executor_runs', 'stop_reason', 'Почему остановился: shutdown, ctrl+c, error, …');
SELECT okx_exec._comment_column_if_exists('executor_runs', 'extra_json', 'JSON-снимок параметров при старте: TP/SL/timeout ticks');

-- ---------------------------------------------------------------------------
-- positions
-- ---------------------------------------------------------------------------
COMMENT ON TABLE okx_exec.positions IS
    'Позиция стратегии: от открытия до закрытия. Одна логическая сделка = одна строка position_id. Обновляется при close/reconcile.';

SELECT okx_exec._comment_column_if_exists('positions', 'position_id', 'PK: pos-{uuid} или pos-ex-{okx_pos_id} после reconcile');
SELECT okx_exec._comment_column_if_exists('positions', 'run_id', 'FK → executor_runs: в каком запуске открыта/закрыта');
SELECT okx_exec._comment_column_if_exists('positions', 'inst_id', 'Инструмент OKX (BTC-USDT-SWAP)');
SELECT okx_exec._comment_column_if_exists('positions', 'strategy_name', 'Стратегия-владелец (strategy_id для аналитики)');
SELECT okx_exec._comment_column_if_exists('positions', 'model_name', 'ML-модель; baseline — пусто');
SELECT okx_exec._comment_column_if_exists('positions', 'side', 'long | short');
SELECT okx_exec._comment_column_if_exists('positions', 'status', 'open | closed | reconciled | sync_lost');
SELECT okx_exec._comment_column_if_exists('positions', 'qty', 'Размер позиции при открытии (контракты)');
SELECT okx_exec._comment_column_if_exists('positions', 'qty_open', 'Остаток открытой позиции; при полном close → 0');
SELECT okx_exec._comment_column_if_exists('positions', 'entry_signal_id', 'signal_id решения на вход (rb-…); цепочка для аналитики');
SELECT okx_exec._comment_column_if_exists('positions', 'entry_order_id_local', 'clOrdId входного ордера (может быть entry-… после reprice)');
SELECT okx_exec._comment_column_if_exists('positions', 'exit_order_id_local', 'clOrdId финального exit-ордера');
SELECT okx_exec._comment_column_if_exists('positions', 'entry_price', 'Средняя цена входа (avgPx с OKX)');
SELECT okx_exec._comment_column_if_exists('positions', 'exit_price', 'Цена выхода; NULL пока позиция open');
SELECT okx_exec._comment_column_if_exists('positions', 'take_profit_ticks', 'Снимок TP в тиках на момент открытия');
SELECT okx_exec._comment_column_if_exists('positions', 'stop_loss_ticks', 'Снимок SL в тиках');
SELECT okx_exec._comment_column_if_exists('positions', 'timeout_sec', 'Снимок max hold time (сек)');
SELECT okx_exec._comment_column_if_exists('positions', 'max_favorable_price', 'MFE: лучшая цена в пользу позиции (резерв)');
SELECT okx_exec._comment_column_if_exists('positions', 'max_adverse_price', 'MAE: худшая цена против позиции (резерв)');
SELECT okx_exec._comment_column_if_exists('positions', 'mfe_ticks', 'MFE в тиках (резерв)');
SELECT okx_exec._comment_column_if_exists('positions', 'mae_ticks', 'MAE в тиках (резерв)');
SELECT okx_exec._comment_column_if_exists('positions', 'entry_ts', 'UTC: открытие позиции');
SELECT okx_exec._comment_column_if_exists('positions', 'exit_ts', 'UTC: закрытие; NULL пока open');
SELECT okx_exec._comment_column_if_exists('positions', 'exit_reason', 'tp | sl | timeout | reconcile | …');
SELECT okx_exec._comment_column_if_exists('positions', 'notes', 'Свободный текст / диагностика');
SELECT okx_exec._comment_column_if_exists('positions', 'created_at', 'UTC: первая запись в БД');
SELECT okx_exec._comment_column_if_exists('positions', 'updated_at', 'UTC: последнее UPDATE (триггер при close)');

-- ---------------------------------------------------------------------------
-- trade_results
-- ---------------------------------------------------------------------------
COMMENT ON TABLE okx_exec.trade_results IS
    'Итог round-trip сделки (1:1 с position_id). Главное для baseline: net_pnl, fees, exit_reason, execution metrics. Сравнение с ML — по этой таблице.';

SELECT okx_exec._comment_column_if_exists('trade_results', 'trade_id', 'PK: trade-{position_id}');
SELECT okx_exec._comment_column_if_exists('trade_results', 'run_id', 'FK → executor_runs');
SELECT okx_exec._comment_column_if_exists('trade_results', 'position_id', 'FK → positions (обязателен до INSERT)');
SELECT okx_exec._comment_column_if_exists('trade_results', 'inst_id', 'Инструмент');
SELECT okx_exec._comment_column_if_exists('trade_results', 'strategy_name', 'Стратегия (strategy_id)');
SELECT okx_exec._comment_column_if_exists('trade_results', 'model_name', 'ML-модель; baseline — пусто');
SELECT okx_exec._comment_column_if_exists('trade_results', 'side', 'long | short');
SELECT okx_exec._comment_column_if_exists('trade_results', 'entry_signal_id', 'Исходный signal_id входа (сохраняется при entry reprice)');
SELECT okx_exec._comment_column_if_exists('trade_results', 'entry_order_id_local', 'clOrdId входа');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_order_id_local', 'clOrdId выхода');
SELECT okx_exec._comment_column_if_exists('trade_results', 'entry_price', 'Цена входа');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_price', 'Цена выхода');
SELECT okx_exec._comment_column_if_exists('trade_results', 'qty', 'Размер (контракты)');
SELECT okx_exec._comment_column_if_exists('trade_results', 'gross_pnl', 'PnL до комиссий: (exit-entry)×size с учётом side');
SELECT okx_exec._comment_column_if_exists('trade_results', 'fees_total', 'entry_fee + exit_fee');
SELECT okx_exec._comment_column_if_exists('trade_results', 'funding_total', 'Funding swap (пока 0)');
SELECT okx_exec._comment_column_if_exists('trade_results', 'slippage_total', 'Резерв под агрегат slippage');
SELECT okx_exec._comment_column_if_exists('trade_results', 'net_pnl', 'gross_pnl − fees_total — ключевая метрика baseline');
SELECT okx_exec._comment_column_if_exists('trade_results', 'holding_seconds', 'exit_ts − entry_ts в секундах');
SELECT okx_exec._comment_column_if_exists('trade_results', 'entry_ts', 'UTC открытия');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_ts', 'UTC закрытия');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_reason', 'tp | sl | timeout | reconcile');
SELECT okx_exec._comment_column_if_exists('trade_results', 'win_flag', 'TRUE если net_pnl > 0');
SELECT okx_exec._comment_column_if_exists('trade_results', 'extra_json', 'JSON: полный снимок execution metrics');
SELECT okx_exec._comment_column_if_exists('trade_results', 'created_at', 'UTC записи строки');

-- колонки migration 005 (пропускаются, если 005 не применена)
SELECT okx_exec._comment_column_if_exists('trade_results', 'entry_fee', 'Комиссия входа (USDT)');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_fee', 'Комиссия выхода');
SELECT okx_exec._comment_column_if_exists('trade_results', 'fee_ccy', 'Валюта комиссии (USDT)');
SELECT okx_exec._comment_column_if_exists('trade_results', 'entry_liquidity', 'maker | taker на входе');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_liquidity', 'maker | taker на выходе');
SELECT okx_exec._comment_column_if_exists('trade_results', 'fee_source', 'okx_fill | estimated_config | missing');
SELECT okx_exec._comment_column_if_exists('trade_results', 'fee_status', 'ok | pending');
SELECT okx_exec._comment_column_if_exists('trade_results', 'close_source', 'executor_maker | executor_market_fallback | okx_reconcile');
SELECT okx_exec._comment_column_if_exists('trade_results', 'entry_order_count', 'Сколько entry-ордеров (с reprice)');
SELECT okx_exec._comment_column_if_exists('trade_results', 'entry_reprice_count', 'Сколько раз переставляли entry');
SELECT okx_exec._comment_column_if_exists('trade_results', 'entry_cancel_count', 'Сколько отмен entry');
SELECT okx_exec._comment_column_if_exists('trade_results', 'entry_wait_sec', 'Секунд до fill входа');
SELECT okx_exec._comment_column_if_exists('trade_results', 'entry_filled_px', 'Фактическая цена fill входа');
SELECT okx_exec._comment_column_if_exists('trade_results', 'entry_first_px', 'Первая лимитная цена entry');
SELECT okx_exec._comment_column_if_exists('trade_results', 'entry_last_px', 'Последняя лимитная цена entry');
SELECT okx_exec._comment_column_if_exists('trade_results', 'entry_slippage_ticks', 'Отклонение fill входа от touch (тики)');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_order_count', 'Сколько exit-ордеров');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_reprice_count', 'Сколько reprice на выходе');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_cancel_count', 'Сколько отмен exit');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_wait_sec', 'Секунд до fill выхода');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_filled_px', 'Фактическая цена fill выхода');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_first_px', 'Первая лимитная цена exit');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_last_px', 'Последняя лимитная цена exit');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_slippage_ticks', 'Отклонение fill выхода от touch (тики)');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_market_fallback_used', 'TRUE = выход через market reduce-only');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_market_fallback_reason', 'Почему fallback: tp/sl/timeout');
SELECT okx_exec._comment_column_if_exists('trade_results', 'exit_maker_attempts', 'Неудачные maker exit до fallback');
SELECT okx_exec._comment_column_if_exists('trade_results', 'timeout_triggered', 'TRUE = выход по timeout');
SELECT okx_exec._comment_column_if_exists('trade_results', 'final_exit_reason', 'Нормализованная причина: tp/sl/timeout/reconcile');

DROP FUNCTION okx_exec._comment_column_if_exists(TEXT, TEXT, TEXT);

-- Проверка: сколько колонок с комментарием
SELECT
    c.table_name,
    count(*) FILTER (WHERE pgd.description IS NOT NULL) AS cols_with_comment,
    count(*) AS cols_total
FROM information_schema.columns c
LEFT JOIN pg_catalog.pg_statio_all_tables st
    ON st.schemaname = 'okx_exec' AND st.relname = c.table_name
LEFT JOIN pg_catalog.pg_description pgd
    ON pgd.objoid = st.relid AND pgd.objsubid = c.ordinal_position
WHERE c.table_schema = 'okx_exec'
  AND c.table_name IN ('executor_runs', 'positions', 'trade_results')
GROUP BY c.table_name
ORDER BY c.table_name;
