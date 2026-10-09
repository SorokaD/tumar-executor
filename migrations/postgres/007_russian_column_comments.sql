-- =============================================================================
-- 007: русские описания таблиц и колонок схемы okx_exec
-- Идемпотентно: можно запускать повторно (COMMENT ON перезаписывает текст).
--
-- psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/postgres/007_russian_column_comments.sql
-- или: python -c "... conn.execute(open('007...').read())"
-- =============================================================================

COMMENT ON SCHEMA okx_exec IS
    'Журнал и аналитика OKX HFT Executor: сигналы, ордера, позиции, сделки, запуски.';

-- ---------------------------------------------------------------------------
-- strategies_registry
-- ---------------------------------------------------------------------------
COMMENT ON TABLE okx_exec.strategies_registry IS
    'Реестр стратегий: желаемое (enabled/disabled) и фактическое runtime-состояние.';

COMMENT ON COLUMN okx_exec.strategies_registry.strategy_name IS 'Уникальное имя стратегии (например random_baseline_v1)';
COMMENT ON COLUMN okx_exec.strategies_registry.inst_id IS 'Торгуемый инструмент OKX (например BTC-USDT-SWAP)';
COMMENT ON COLUMN okx_exec.strategies_registry.desired_state IS 'Желаемое состояние: enabled или disabled';
COMMENT ON COLUMN okx_exec.strategies_registry.runtime_state IS 'Фактическое состояние процесса: running, stopped, error';
COMMENT ON COLUMN okx_exec.strategies_registry.updated_at IS 'Время последнего обновления записи';

-- ---------------------------------------------------------------------------
-- strategy_commands
-- ---------------------------------------------------------------------------
COMMENT ON TABLE okx_exec.strategy_commands IS
    'Очередь команд control-api: enable, disable, restart.';

COMMENT ON COLUMN okx_exec.strategy_commands.command_id IS 'Суррогатный ID команды';
COMMENT ON COLUMN okx_exec.strategy_commands.strategy_name IS 'Целевая стратегия';
COMMENT ON COLUMN okx_exec.strategy_commands.command_type IS 'Тип команды: enable, disable, restart';
COMMENT ON COLUMN okx_exec.strategy_commands.command_mode IS 'Режим disable: drain или force';
COMMENT ON COLUMN okx_exec.strategy_commands.created_at IS 'Время постановки команды в очередь';
COMMENT ON COLUMN okx_exec.strategy_commands.processed_at IS 'Время обработки executor';
COMMENT ON COLUMN okx_exec.strategy_commands.status IS 'Статус: pending, processing, done, failed';
COMMENT ON COLUMN okx_exec.strategy_commands.error_text IS 'Текст ошибки при status=failed';

-- ---------------------------------------------------------------------------
-- executor_runs
-- ---------------------------------------------------------------------------
COMMENT ON TABLE okx_exec.executor_runs IS
    'Запуски процесса executor. Группировка всех событий по run_id. Не очищать при сбросе торговых данных.';

COMMENT ON COLUMN okx_exec.executor_runs.run_id IS 'Суррогатный ID запуска (PK)';
COMMENT ON COLUMN okx_exec.executor_runs.run_uuid IS 'UUID запуска для глобальной идентификации';
COMMENT ON COLUMN okx_exec.executor_runs.started_at IS 'Время старта процесса';
COMMENT ON COLUMN okx_exec.executor_runs.finished_at IS 'Время завершения (NULL пока running)';
COMMENT ON COLUMN okx_exec.executor_runs.runtime_mode IS 'Режим: live, paper, replay';
COMMENT ON COLUMN okx_exec.executor_runs.environment_name IS 'Имя окружения из .env';
COMMENT ON COLUMN okx_exec.executor_runs.host_name IS 'Имя хоста VPS';
COMMENT ON COLUMN okx_exec.executor_runs.process_id IS 'PID процесса ОС';
COMMENT ON COLUMN okx_exec.executor_runs.app_version IS 'Версия приложения';
COMMENT ON COLUMN okx_exec.executor_runs.strategy_name IS 'Основная стратегия этого запуска';
COMMENT ON COLUMN okx_exec.executor_runs.model_name IS 'Имя ML-модели (если применимо)';
COMMENT ON COLUMN okx_exec.executor_runs.inst_id IS 'Инструмент запуска';
COMMENT ON COLUMN okx_exec.executor_runs.status IS 'Статус: running, stopped, error, draining';
COMMENT ON COLUMN okx_exec.executor_runs.stop_reason IS 'Причина остановки (shutdown, error, …)';
COMMENT ON COLUMN okx_exec.executor_runs.extra_json IS 'Снимок параметров стратегии при старте (TP/SL/timeout)';

-- ---------------------------------------------------------------------------
-- strategy_signals
-- ---------------------------------------------------------------------------
COMMENT ON TABLE okx_exec.strategy_signals IS
    'Решения стратегии: сигнал на вход/выход/пропуск. signal_id = clOrdId на entry.';

COMMENT ON COLUMN okx_exec.strategy_signals.signal_id IS 'ID сигнала (rb-… для random baseline)';
COMMENT ON COLUMN okx_exec.strategy_signals.run_id IS 'FK на executor_runs';
COMMENT ON COLUMN okx_exec.strategy_signals.ts_decision IS 'Время принятия решения';
COMMENT ON COLUMN okx_exec.strategy_signals.strategy_name IS 'Имя стратегии';
COMMENT ON COLUMN okx_exec.strategy_signals.model_name IS 'Имя модели (для ML-стратегий)';
COMMENT ON COLUMN okx_exec.strategy_signals.inst_id IS 'Инструмент';
COMMENT ON COLUMN okx_exec.strategy_signals.side IS 'Направление: long, short, buy, sell';
COMMENT ON COLUMN okx_exec.strategy_signals.decision_type IS 'Тип: entry, exit, skip, flatten';
COMMENT ON COLUMN okx_exec.strategy_signals.confidence_score IS 'Уверенность модели (0..1)';
COMMENT ON COLUMN okx_exec.strategy_signals.take_profit_ticks IS 'Снимок TP в тиках на момент сигнала';
COMMENT ON COLUMN okx_exec.strategy_signals.stop_loss_ticks IS 'Снимок SL в тиках';
COMMENT ON COLUMN okx_exec.strategy_signals.timeout_sec IS 'Снимок таймаута удержания позиции';
COMMENT ON COLUMN okx_exec.strategy_signals.market_snapshot IS 'JSON: bid, ask, last, spread';
COMMENT ON COLUMN okx_exec.strategy_signals.features_json IS 'JSON: фичи модели';
COMMENT ON COLUMN okx_exec.strategy_signals.reason_code IS 'Код причины решения стратегии';
COMMENT ON COLUMN okx_exec.strategy_signals.created_at IS 'Время записи в БД';

-- ---------------------------------------------------------------------------
-- execution_attempts
-- ---------------------------------------------------------------------------
COMMENT ON TABLE okx_exec.execution_attempts IS
    'Попытки действий executor: submit, cancel, skip, ошибки API.';

COMMENT ON COLUMN okx_exec.execution_attempts.attempt_id IS 'Суррогатный ID попытки';
COMMENT ON COLUMN okx_exec.execution_attempts.attempt_uuid IS 'UUID попытки';
COMMENT ON COLUMN okx_exec.execution_attempts.run_id IS 'FK на executor_runs';
COMMENT ON COLUMN okx_exec.execution_attempts.signal_id IS 'Связанный signal_id (если есть)';
COMMENT ON COLUMN okx_exec.execution_attempts.ts_event IS 'Время события';
COMMENT ON COLUMN okx_exec.execution_attempts.inst_id IS 'Инструмент';
COMMENT ON COLUMN okx_exec.execution_attempts.strategy_name IS 'Стратегия';
COMMENT ON COLUMN okx_exec.execution_attempts.action_type IS 'Действие: submit_order, cancel_order, skip_decision, …';
COMMENT ON COLUMN okx_exec.execution_attempts.side IS 'Сторона ордера: buy/sell';
COMMENT ON COLUMN okx_exec.execution_attempts.status IS 'Результат: ok, error, skipped, rejected';
COMMENT ON COLUMN okx_exec.execution_attempts.skip_reason IS 'Причина пропуска';
COMMENT ON COLUMN okx_exec.execution_attempts.reject_reason IS 'Причина отклонения risk/guard';
COMMENT ON COLUMN okx_exec.execution_attempts.error_code IS 'Код ошибки OKX (sCode)';
COMMENT ON COLUMN okx_exec.execution_attempts.error_message IS 'Текст ошибки';
COMMENT ON COLUMN okx_exec.execution_attempts.request_payload IS 'JSON тела запроса к бирже';
COMMENT ON COLUMN okx_exec.execution_attempts.response_payload IS 'JSON ответа биржи';

-- ---------------------------------------------------------------------------
-- orders
-- ---------------------------------------------------------------------------
COMMENT ON TABLE okx_exec.orders IS
    'Ордера на бирже. Каждый submit и reprice — отдельная строка (append-only).';

COMMENT ON COLUMN okx_exec.orders.order_pk IS 'Суррогатный PK (с ts_created)';
COMMENT ON COLUMN okx_exec.orders.order_id_local IS 'Локальный client order id (clOrdId)';
COMMENT ON COLUMN okx_exec.orders.run_id IS 'FK на executor_runs';
COMMENT ON COLUMN okx_exec.orders.signal_id IS 'Исходный signal_id входа (сохраняется при reprice)';
COMMENT ON COLUMN okx_exec.orders.attempt_id IS 'Связь с execution_attempts';
COMMENT ON COLUMN okx_exec.orders.position_id IS 'Позиция (для exit-ордеров)';
COMMENT ON COLUMN okx_exec.orders.trade_id IS 'ID сделки после закрытия';
COMMENT ON COLUMN okx_exec.orders.inst_id IS 'Инструмент';
COMMENT ON COLUMN okx_exec.orders.strategy_name IS 'Стратегия';
COMMENT ON COLUMN okx_exec.orders.exchange_name IS 'Биржа (okx)';
COMMENT ON COLUMN okx_exec.orders.order_id_exchange IS 'ordId на OKX';
COMMENT ON COLUMN okx_exec.orders.cl_ord_id IS 'clOrdId на OKX (дубликат order_id_local)';
COMMENT ON COLUMN okx_exec.orders.parent_order_id_local IS 'Предыдущий clOrdId при reprice';
COMMENT ON COLUMN okx_exec.orders.side IS 'buy или sell';
COMMENT ON COLUMN okx_exec.orders.position_action IS 'open, close, reduce, unknown';
COMMENT ON COLUMN okx_exec.orders.ord_type IS 'post_only, market, limit, …';
COMMENT ON COLUMN okx_exec.orders.td_mode IS 'Режим маржи: isolated, cross';
COMMENT ON COLUMN okx_exec.orders.pos_side IS 'Сторона позиции OKX (net mode обычно пусто)';
COMMENT ON COLUMN okx_exec.orders.reduce_only IS 'Только уменьшение позиции';
COMMENT ON COLUMN okx_exec.orders.price IS 'Лимитная цена заявки';
COMMENT ON COLUMN okx_exec.orders.size IS 'Размер заявки (контракты)';
COMMENT ON COLUMN okx_exec.orders.filled_size IS 'Исполненный объём';
COMMENT ON COLUMN okx_exec.orders.avg_fill_price IS 'Средняя цена исполнения';
COMMENT ON COLUMN okx_exec.orders.status IS 'submitted, live, filled, canceled, rejected';
COMMENT ON COLUMN okx_exec.orders.exchange_code IS 'Код ответа OKX';
COMMENT ON COLUMN okx_exec.orders.exchange_message IS 'Сообщение OKX';
COMMENT ON COLUMN okx_exec.orders.ts_created IS 'Время создания записи';
COMMENT ON COLUMN okx_exec.orders.ts_submitted IS 'Время отправки на биржу';
COMMENT ON COLUMN okx_exec.orders.ts_ack IS 'Время подтверждения биржей';
COMMENT ON COLUMN okx_exec.orders.ts_first_fill IS 'Время первого fill';
COMMENT ON COLUMN okx_exec.orders.ts_last_fill IS 'Время последнего fill';
COMMENT ON COLUMN okx_exec.orders.ts_canceled IS 'Время отмены';
COMMENT ON COLUMN okx_exec.orders.ts_closed IS 'Время финального статуса';
COMMENT ON COLUMN okx_exec.orders.raw_request_json IS 'Сырой JSON запроса';
COMMENT ON COLUMN okx_exec.orders.raw_response_json IS 'Сырой JSON ответа';

-- ---------------------------------------------------------------------------
-- order_fills
-- ---------------------------------------------------------------------------
COMMENT ON TABLE okx_exec.order_fills IS
    'Исполнения ордеров: цена, объём, комиссия, maker/taker.';

COMMENT ON COLUMN okx_exec.order_fills.fill_pk IS 'Суррогатный PK';
COMMENT ON COLUMN okx_exec.order_fills.fill_id_exchange IS 'tradeId/billId на OKX';
COMMENT ON COLUMN okx_exec.order_fills.order_pk IS 'FK на orders.order_pk';
COMMENT ON COLUMN okx_exec.order_fills.order_id_local IS 'clOrdId ордера';
COMMENT ON COLUMN okx_exec.order_fills.order_id_exchange IS 'ordId на OKX';
COMMENT ON COLUMN okx_exec.order_fills.run_id IS 'FK на executor_runs';
COMMENT ON COLUMN okx_exec.order_fills.inst_id IS 'Инструмент';
COMMENT ON COLUMN okx_exec.order_fills.strategy_name IS 'Стратегия';
COMMENT ON COLUMN okx_exec.order_fills.side IS 'buy/sell';
COMMENT ON COLUMN okx_exec.order_fills.fill_price IS 'Цена исполнения';
COMMENT ON COLUMN okx_exec.order_fills.fill_size IS 'Объём исполнения';
COMMENT ON COLUMN okx_exec.order_fills.fill_notional IS 'Нотионал fill (price × size)';
COMMENT ON COLUMN okx_exec.order_fills.liquidity_side IS 'maker или taker';
COMMENT ON COLUMN okx_exec.order_fills.fee IS 'Комиссия (обычно отрицательная на OKX)';
COMMENT ON COLUMN okx_exec.order_fills.fee_ccy IS 'Валюта комиссии (USDT)';
COMMENT ON COLUMN okx_exec.order_fills.pnl_realized_exchange IS 'Realized PnL с биржи (если есть)';
COMMENT ON COLUMN okx_exec.order_fills.ts_fill IS 'Время исполнения';
COMMENT ON COLUMN okx_exec.order_fills.raw_fill_json IS 'Сырой JSON fill с OKX';

-- ---------------------------------------------------------------------------
-- positions
-- ---------------------------------------------------------------------------
COMMENT ON TABLE okx_exec.positions IS
    'Жизненный цикл позиции: открытие, удержание, закрытие, reconcile.';

COMMENT ON COLUMN okx_exec.positions.position_id IS 'Локальный ID (pos-… или pos-ex-… после reconcile)';
COMMENT ON COLUMN okx_exec.positions.run_id IS 'FK на executor_runs';
COMMENT ON COLUMN okx_exec.positions.inst_id IS 'Инструмент';
COMMENT ON COLUMN okx_exec.positions.strategy_name IS 'Стратегия';
COMMENT ON COLUMN okx_exec.positions.model_name IS 'Имя модели';
COMMENT ON COLUMN okx_exec.positions.side IS 'long или short';
COMMENT ON COLUMN okx_exec.positions.status IS 'open, closed, reconciled, sync_lost';
COMMENT ON COLUMN okx_exec.positions.qty IS 'Размер при открытии';
COMMENT ON COLUMN okx_exec.positions.qty_open IS 'Текущий открытый остаток';
COMMENT ON COLUMN okx_exec.positions.entry_signal_id IS 'signal_id входа';
COMMENT ON COLUMN okx_exec.positions.entry_order_id_local IS 'clOrdId входного ордера';
COMMENT ON COLUMN okx_exec.positions.exit_order_id_local IS 'clOrdId выходного ордера';
COMMENT ON COLUMN okx_exec.positions.entry_price IS 'Средняя цена входа';
COMMENT ON COLUMN okx_exec.positions.exit_price IS 'Цена выхода';
COMMENT ON COLUMN okx_exec.positions.take_profit_ticks IS 'Снимок TP (тики)';
COMMENT ON COLUMN okx_exec.positions.stop_loss_ticks IS 'Снимок SL (тики)';
COMMENT ON COLUMN okx_exec.positions.timeout_sec IS 'Снимок таймаута';
COMMENT ON COLUMN okx_exec.positions.max_favorable_price IS 'Лучшая цена в пользу позиции (MFE)';
COMMENT ON COLUMN okx_exec.positions.max_adverse_price IS 'Худшая цена против позиции (MAE)';
COMMENT ON COLUMN okx_exec.positions.mfe_ticks IS 'MFE в тиках';
COMMENT ON COLUMN okx_exec.positions.mae_ticks IS 'MAE в тиках';
COMMENT ON COLUMN okx_exec.positions.entry_ts IS 'Время открытия';
COMMENT ON COLUMN okx_exec.positions.exit_ts IS 'Время закрытия';
COMMENT ON COLUMN okx_exec.positions.exit_reason IS 'tp, sl, timeout, reconcile, …';
COMMENT ON COLUMN okx_exec.positions.notes IS 'Произвольные заметки';
COMMENT ON COLUMN okx_exec.positions.created_at IS 'Время создания записи';
COMMENT ON COLUMN okx_exec.positions.updated_at IS 'Время последнего обновления';

-- ---------------------------------------------------------------------------
-- trade_results
-- ---------------------------------------------------------------------------
COMMENT ON TABLE okx_exec.trade_results IS
    'Итог завершённой сделки: gross/net PnL, комиссии, качество исполнения, причина выхода.';

COMMENT ON COLUMN okx_exec.trade_results.trade_id IS 'PK: trade-{position_id}';
COMMENT ON COLUMN okx_exec.trade_results.run_id IS 'FK на executor_runs';
COMMENT ON COLUMN okx_exec.trade_results.position_id IS 'FK на positions';
COMMENT ON COLUMN okx_exec.trade_results.inst_id IS 'Инструмент';
COMMENT ON COLUMN okx_exec.trade_results.strategy_name IS 'Стратегия (strategy_id)';
COMMENT ON COLUMN okx_exec.trade_results.model_name IS 'Имя модели';
COMMENT ON COLUMN okx_exec.trade_results.side IS 'long или short';
COMMENT ON COLUMN okx_exec.trade_results.entry_signal_id IS 'Исходный signal_id входа';
COMMENT ON COLUMN okx_exec.trade_results.entry_order_id_local IS 'clOrdId входа';
COMMENT ON COLUMN okx_exec.trade_results.exit_order_id_local IS 'clOrdId выхода';
COMMENT ON COLUMN okx_exec.trade_results.entry_price IS 'Цена входа (средняя)';
COMMENT ON COLUMN okx_exec.trade_results.exit_price IS 'Цена выхода (средняя)';
COMMENT ON COLUMN okx_exec.trade_results.qty IS 'Размер позиции';
COMMENT ON COLUMN okx_exec.trade_results.gross_pnl IS 'PnL до комиссий';
COMMENT ON COLUMN okx_exec.trade_results.fees_total IS 'Суммарная комиссия (entry + exit)';
COMMENT ON COLUMN okx_exec.trade_results.funding_total IS 'Funding (пока не используется)';
COMMENT ON COLUMN okx_exec.trade_results.slippage_total IS 'Суммарное проскальзывание (резерв)';
COMMENT ON COLUMN okx_exec.trade_results.net_pnl IS 'PnL после комиссий — главная метрика baseline';
COMMENT ON COLUMN okx_exec.trade_results.holding_seconds IS 'Время удержания позиции (сек)';
COMMENT ON COLUMN okx_exec.trade_results.entry_ts IS 'Время открытия';
COMMENT ON COLUMN okx_exec.trade_results.exit_ts IS 'Время закрытия';
COMMENT ON COLUMN okx_exec.trade_results.exit_reason IS 'Причина выхода: tp, sl, timeout, reconcile';
COMMENT ON COLUMN okx_exec.trade_results.win_flag IS 'TRUE если net_pnl > 0';
COMMENT ON COLUMN okx_exec.trade_results.extra_json IS 'Полный JSON execution metrics';
COMMENT ON COLUMN okx_exec.trade_results.created_at IS 'Время записи итога';
COMMENT ON COLUMN okx_exec.trade_results.entry_fee IS 'Комиссия на входе';
COMMENT ON COLUMN okx_exec.trade_results.exit_fee IS 'Комиссия на выходе';
COMMENT ON COLUMN okx_exec.trade_results.fee_ccy IS 'Валюта комиссии';
COMMENT ON COLUMN okx_exec.trade_results.entry_liquidity IS 'maker/taker на входе';
COMMENT ON COLUMN okx_exec.trade_results.exit_liquidity IS 'maker/taker на выходе';
COMMENT ON COLUMN okx_exec.trade_results.fee_source IS 'okx_fill, estimated_config или missing';
COMMENT ON COLUMN okx_exec.trade_results.fee_status IS 'ok, pending';
COMMENT ON COLUMN okx_exec.trade_results.close_source IS 'executor_maker, executor_market_fallback, okx_reconcile';
COMMENT ON COLUMN okx_exec.trade_results.entry_order_count IS 'Число entry-ордеров (включая reprice)';
COMMENT ON COLUMN okx_exec.trade_results.entry_reprice_count IS 'Число перестановок entry';
COMMENT ON COLUMN okx_exec.trade_results.entry_cancel_count IS 'Число отмен entry';
COMMENT ON COLUMN okx_exec.trade_results.entry_wait_sec IS 'Секунд от submit до fill входа';
COMMENT ON COLUMN okx_exec.trade_results.entry_filled_px IS 'Фактическая цена fill входа';
COMMENT ON COLUMN okx_exec.trade_results.entry_first_px IS 'Первая цена entry-ордера';
COMMENT ON COLUMN okx_exec.trade_results.entry_last_px IS 'Последняя цена entry перед fill';
COMMENT ON COLUMN okx_exec.trade_results.entry_slippage_ticks IS 'Проскальзывание входа от touch (тики)';
COMMENT ON COLUMN okx_exec.trade_results.exit_order_count IS 'Число exit-ордеров';
COMMENT ON COLUMN okx_exec.trade_results.exit_reprice_count IS 'Число перестановок exit';
COMMENT ON COLUMN okx_exec.trade_results.exit_cancel_count IS 'Число отмен exit';
COMMENT ON COLUMN okx_exec.trade_results.exit_wait_sec IS 'Секунд от submit до fill выхода';
COMMENT ON COLUMN okx_exec.trade_results.exit_filled_px IS 'Фактическая цена fill выхода';
COMMENT ON COLUMN okx_exec.trade_results.exit_first_px IS 'Первая цена exit-ордера';
COMMENT ON COLUMN okx_exec.trade_results.exit_last_px IS 'Последняя цена exit перед fill';
COMMENT ON COLUMN okx_exec.trade_results.exit_slippage_ticks IS 'Проскальзывание выхода от touch (тики)';
COMMENT ON COLUMN okx_exec.trade_results.exit_market_fallback_used IS 'Был ли market reduce-only fallback';
COMMENT ON COLUMN okx_exec.trade_results.exit_market_fallback_reason IS 'Причина fallback (tp/sl/timeout)';
COMMENT ON COLUMN okx_exec.trade_results.exit_maker_attempts IS 'Неудачные попытки maker exit';
COMMENT ON COLUMN okx_exec.trade_results.timeout_triggered IS 'TRUE если выход инициирован timeout';
COMMENT ON COLUMN okx_exec.trade_results.final_exit_reason IS 'Нормализованная причина: tp, sl, timeout, reconcile';

-- ---------------------------------------------------------------------------
-- reconciliation_events
-- ---------------------------------------------------------------------------
COMMENT ON TABLE okx_exec.reconciliation_events IS
    'События сверки локального состояния executor с биржей OKX.';

COMMENT ON COLUMN okx_exec.reconciliation_events.reconciliation_id IS 'Суррогатный ID';
COMMENT ON COLUMN okx_exec.reconciliation_events.run_id IS 'FK на executor_runs';
COMMENT ON COLUMN okx_exec.reconciliation_events.ts_event IS 'Время события';
COMMENT ON COLUMN okx_exec.reconciliation_events.inst_id IS 'Инструмент';
COMMENT ON COLUMN okx_exec.reconciliation_events.strategy_name IS 'Стратегия';
COMMENT ON COLUMN okx_exec.reconciliation_events.severity IS 'info, warning, error';
COMMENT ON COLUMN okx_exec.reconciliation_events.mismatch_type IS 'Тип расхождения (position_reconciled, …)';
COMMENT ON COLUMN okx_exec.reconciliation_events.local_entity_type IS 'Тип локальной сущности';
COMMENT ON COLUMN okx_exec.reconciliation_events.local_entity_id IS 'ID локальной сущности';
COMMENT ON COLUMN okx_exec.reconciliation_events.exchange_entity_id IS 'ID на бирже';
COMMENT ON COLUMN okx_exec.reconciliation_events.resolution_status IS 'detected, resolved, …';
COMMENT ON COLUMN okx_exec.reconciliation_events.message IS 'Человекочитаемое описание';
COMMENT ON COLUMN okx_exec.reconciliation_events.payload_json IS 'Дополнительный JSON';

-- ---------------------------------------------------------------------------
-- service_events
-- ---------------------------------------------------------------------------
COMMENT ON TABLE okx_exec.service_events IS
    'Сервисные события: аудит, диагностика, не дублирует execution_attempts.';

COMMENT ON COLUMN okx_exec.service_events.event_id IS 'Суррогатный ID';
COMMENT ON COLUMN okx_exec.service_events.event_uuid IS 'UUID события';
COMMENT ON COLUMN okx_exec.service_events.run_id IS 'FK на executor_runs';
COMMENT ON COLUMN okx_exec.service_events.ts_event IS 'Время события';
COMMENT ON COLUMN okx_exec.service_events.severity IS 'info, warning, error';
COMMENT ON COLUMN okx_exec.service_events.component IS 'Компонент: executor, control-api';
COMMENT ON COLUMN okx_exec.service_events.event_type IS 'Тип: decision, entry_submitted, position_closed, …';
COMMENT ON COLUMN okx_exec.service_events.strategy_name IS 'Стратегия или system';
COMMENT ON COLUMN okx_exec.service_events.inst_id IS 'Инструмент';
COMMENT ON COLUMN okx_exec.service_events.signal_id IS 'Связанный signal_id';
COMMENT ON COLUMN okx_exec.service_events.attempt_id IS 'Связанная попытка';
COMMENT ON COLUMN okx_exec.service_events.position_id IS 'Связанная позиция';
COMMENT ON COLUMN okx_exec.service_events.trade_id IS 'Связанная сделка';
COMMENT ON COLUMN okx_exec.service_events.message IS 'Краткое сообщение';
COMMENT ON COLUMN okx_exec.service_events.payload_json IS 'JSON с деталями';

-- ---------------------------------------------------------------------------
-- v_trade_daily_summary (view)
-- ---------------------------------------------------------------------------
COMMENT ON VIEW okx_exec.v_trade_daily_summary IS
    'Дневная агрегация trade_results: winrate, net PnL, fees, market_fallback_ratio, maker ratios.';

COMMENT ON COLUMN okx_exec.v_trade_daily_summary.trade_day IS 'Календарный день (UTC) по exit_ts';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.strategy_name IS 'Стратегия';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.run_id IS 'Запуск executor';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.inst_id IS 'Инструмент';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.trades_count IS 'Число завершённых сделок';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.winrate_gross IS 'Доля сделок с gross_pnl > 0';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.winrate_net IS 'Доля сделок с net_pnl > 0';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.gross_pnl_sum IS 'Сумма gross PnL';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.net_pnl_sum IS 'Сумма net PnL';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.total_fee_sum IS 'Сумма комиссий';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.avg_fee_per_trade IS 'Средняя комиссия на сделку';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.avg_hold_sec IS 'Среднее время удержания';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.median_hold_sec IS 'Медиана времени удержания';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.tp_count IS 'Сделок закрыто по TP';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.sl_count IS 'Сделок закрыто по SL';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.timeout_count IS 'Сделок закрыто по timeout';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.market_fallback_count IS 'Сделок с market fallback на выходе';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.market_fallback_ratio IS 'Доля market fallback';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.maker_entry_ratio IS 'Доля maker на входе';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.maker_exit_ratio IS 'Доля maker на выходе';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.avg_entry_wait_sec IS 'Среднее время ожидания входа';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.avg_exit_wait_sec IS 'Среднее время ожидания выхода';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.avg_entry_reprice_count IS 'Среднее число reprice на входе';
COMMENT ON COLUMN okx_exec.v_trade_daily_summary.avg_exit_reprice_count IS 'Среднее число reprice на выходе';
