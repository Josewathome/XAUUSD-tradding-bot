//+------------------------------------------------------------------+
//|  ZoneBotBridge.mq5 v4 — File-Based Transport                    |
//|                                                                  |
//|  WHY FILES INSTEAD OF SOCKETS                                    |
//|  v3 used MT5's built-in SocketCreate/SocketConnect to avoid the  |
//|  DLL-import permission dialog. But SocketConnect() — even to     |
//|  127.0.0.1 — still requires the target in "Allow WebRequest for  |
//|  listed URL", and this MT5 build does not persist that setting   |
//|  across ANY process restart (confirmed: neither a forced kill    |
//|  nor a graceful shutdown with a flush window preserves it, and   |
//|  exhaustive search of every ini/dat/reg file in the Wine prefix  |
//|  found no trace of it anywhere — it is simply not written to     |
//|  disk). That made the EA need a human to re-click the setting    |
//|  after every restart, which isn't viable for unattended trading. |
//|                                                                  |
//|  FileOpen/FileWrite to the EA's own sandbox (MQL5/Files/) needs  |
//|  NO special permission beyond "Allow algorithmic trading" — the  |
//|  one switch that IS confirmed to survive restarts. This version  |
//|  writes events to a file instead of a socket; Python tails it.   |
//|                                                                  |
//|  TRANSPORT                                                       |
//|  EA appends newline-delimited "TOPIC {JSON}\n" lines to a file   |
//|  in its own MQL5/Files/ sandbox. Same topics as v3: TICK /       |
//|  BAR_CLOSE / BAR_OPEN / TRADE / HEARTBEAT. Python polls the file |
//|  by byte offset (see zmq_feed.py).                               |
//+------------------------------------------------------------------+
#property copyright "ZoneBot"
#property version   "4.00"
#property strict

input string   FEED_FILE      = "zonebot_feed.jsonl";
input string   SYMBOLS_CSV    = "XAUUSD,EURUSD";
input int      HEARTBEAT_SECS = 5;
input long     MAX_FILE_BYTES = 20000000;   // rotate (truncate) past ~20MB

int      g_file = INVALID_HANDLE;

string   g_symbols[];
int      g_sym_count = 0;

string   g_bar_keys[];
datetime g_bar_last_open[];
int      g_bar_count = 0;

datetime g_last_tick_time = 0;

bool _OpenFeedFile()
{
   if(g_file != INVALID_HANDLE) { FileClose(g_file); g_file = INVALID_HANDLE; }
   // FILE_WRITE truncates — a fresh file each EA session, same as a fresh
   // TCP connection never replays history. FILE_SHARE_READ/WRITE let
   // Python's independent OS-level file handle read it concurrently.
   g_file = FileOpen(FEED_FILE, FILE_WRITE|FILE_READ|FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_TXT|FILE_ANSI, 0);
   if(g_file == INVALID_HANDLE)
   {
      Print("ZoneBotBridge: FileOpen failed for ", FEED_FILE, " err=", GetLastError());
      return false;
   }
   return true;
}

void _Send(const string msg)
{
   if(g_file == INVALID_HANDLE) return;
   FileSeek(g_file, 0, SEEK_END);
   FileWriteString(g_file, msg + "\n");
   FileFlush(g_file);
   // Bound the file size so a long-running session doesn't grow forever —
   // Python detects the resulting size drop and resets its read offset.
   if(FileSize(g_file) > MAX_FILE_BYTES)
      _OpenFeedFile();
}

int OnInit()
{
   g_sym_count = StringSplit(SYMBOLS_CSV, ',', g_symbols);
   if(g_sym_count == 0) { Print("ZoneBotBridge: no symbols"); return INIT_FAILED; }

   for(int i = 0; i < g_sym_count; i++) SymbolSelect(g_symbols[i], true);

   string tfs[5]; tfs[0]="M1"; tfs[1]="M5"; tfs[2]="M30"; tfs[3]="H1"; tfs[4]="H4";
   ENUM_TIMEFRAMES tf_enum[5];
   tf_enum[0]=PERIOD_M1; tf_enum[1]=PERIOD_M5; tf_enum[2]=PERIOD_M30;
   tf_enum[3]=PERIOD_H1; tf_enum[4]=PERIOD_H4;

   ArrayResize(g_bar_keys,      g_sym_count * 5);
   ArrayResize(g_bar_last_open, g_sym_count * 5);
   int slot = 0;
   for(int s = 0; s < g_sym_count; s++)
      for(int t = 0; t < 5; t++)
      {
         g_bar_keys[slot]      = g_symbols[s] + "_" + tfs[t];
         g_bar_last_open[slot] = iTime(g_symbols[s], tf_enum[t], 0);
         slot++;
      }
   g_bar_count = slot;

   if(!_OpenFeedFile()) return INIT_FAILED;
   EventSetTimer(1);
   Print("ZoneBotBridge v4: writing to MQL5/Files/", FEED_FILE);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(g_file != INVALID_HANDLE) { FileClose(g_file); g_file = INVALID_HANDLE; }
   Print("ZoneBotBridge: stopped (reason=", reason, ")");
}

void OnTick()
{
   if(g_file == INVALID_HANDLE) { _OpenFeedFile(); return; }
   g_last_tick_time = TimeCurrent();

   string tfs[5]; tfs[0]="M1"; tfs[1]="M5"; tfs[2]="M30"; tfs[3]="H1"; tfs[4]="H4";
   ENUM_TIMEFRAMES tf_enum[5];
   tf_enum[0]=PERIOD_M1; tf_enum[1]=PERIOD_M5; tf_enum[2]=PERIOD_M30;
   tf_enum[3]=PERIOD_H1; tf_enum[4]=PERIOD_H4;

   for(int i = 0; i < g_sym_count; i++)
   {
      string sym = g_symbols[i];
      MqlTick tick;
      if(!SymbolInfoTick(sym, tick)) continue;

      int    digits     = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      double pip_size   = (digits==3||digits==5) ? 0.0001 : 0.001;
      if(digits==2) pip_size = 0.1;
      double spread_pips = (tick.ask - tick.bid) / pip_size;

      _Send(StringFormat(
         "TICK {\"sym\":\"%s\",\"bid\":%.5f,\"ask\":%.5f,\"spread\":%.2f,\"time\":%d}",
         sym, tick.bid, tick.ask, spread_pips, (int)tick.time));

      for(int t = 0; t < 5; t++)
      {
         datetime bar0 = iTime(sym, tf_enum[t], 0);
         int slot_idx  = i * 5 + t;
         if(slot_idx < 0 || slot_idx >= g_bar_count) continue;
         if(bar0 != g_bar_last_open[slot_idx])
         {
            _Send(StringFormat(
               "BAR_CLOSE {\"sym\":\"%s\",\"tf\":\"%s\","
               "\"open\":%.5f,\"high\":%.5f,\"low\":%.5f,\"close\":%.5f,"
               "\"vol\":%d,\"time\":%d}",
               sym, tfs[t],
               iOpen(sym,tf_enum[t],1), iHigh(sym,tf_enum[t],1),
               iLow(sym, tf_enum[t],1), iClose(sym,tf_enum[t],1),
               (int)iVolume(sym,tf_enum[t],1), (int)iTime(sym,tf_enum[t],1)));
            _Send(StringFormat(
               "BAR_OPEN {\"sym\":\"%s\",\"tf\":\"%s\",\"time\":%d}",
               sym, tfs[t], (int)bar0));
            g_bar_last_open[slot_idx] = bar0;
         }
      }
   }
}

void OnTimer()
{
   if(g_file == INVALID_HANDLE) { _OpenFeedFile(); return; }
   datetime now = TimeCurrent();
   if(now - g_last_tick_time >= HEARTBEAT_SECS)
   {
      _Send(StringFormat("HEARTBEAT {\"time\":%d}", (int)now));
      g_last_tick_time = now;
   }
}

void OnTradeTransaction(
   const MqlTradeTransaction &trans,
   const MqlTradeRequest     &request,
   const MqlTradeResult      &result)
{
   if(g_file == INVALID_HANDLE) return;
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD &&
      trans.type != TRADE_TRANSACTION_POSITION) return;
   ulong ticket = trans.position;
   if(ticket == 0 || !PositionSelectByTicket(ticket)) return;

   string sym   = PositionGetString(POSITION_SYMBOL);
   int    ptype = (int)PositionGetInteger(POSITION_TYPE);
   _Send(StringFormat(
      "TRADE {\"event\":\"%s\",\"ticket\":%d,\"sym\":\"%s\","
      "\"type\":\"%s\",\"vol\":%.2f,\"price\":%.5f,"
      "\"sl\":%.5f,\"tp\":%.5f,\"profit\":%.2f,\"time\":%d}",
      (trans.type==TRADE_TRANSACTION_DEAL_ADD ? "DEAL" : "MODIFY"),
      (int)ticket, sym, (ptype==0 ? "BUY" : "SELL"),
      PositionGetDouble(POSITION_VOLUME),
      PositionGetDouble(POSITION_PRICE_OPEN),
      PositionGetDouble(POSITION_SL),
      PositionGetDouble(POSITION_TP),
      PositionGetDouble(POSITION_PROFIT),
      (int)TimeCurrent()));
}
