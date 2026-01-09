#property copyright "Cascade Research"
#property link      "https://example.com"
#property version   "1.0"
#property strict

#include <Trade/Trade.mqh>
#include <stdlib/JSON.mqh>

input string InpIntentRoot = "execution_intents";
input int    InpPollSeconds = 1;

CTrade g_trade;
string g_root;
string g_pending;
string g_done;
string g_failed;

int OnInit()
  {
   g_root   = InpIntentRoot;
   g_pending = g_root + "\\pending\\";
   g_done    = g_root + "\\done\\";
   g_failed  = g_root + "\\failed\\";

   if(!EnsureFolders())
     {
      return(INIT_FAILED);
     }

   EventSetTimer(MathMax(InpPollSeconds,1));
   PrintFormat("ExecutionAgent initialized | root=%s poll=%d", g_root, InpPollSeconds);
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

void OnTick()
  {
   // No strategy logic; execution only.
  }

void OnTimer()
  {
   ProcessPending();
  }

bool EnsureFolders()
  {
   return EnsureFolder(g_root)
          && EnsureFolder(g_pending)
          && EnsureFolder(g_done)
          && EnsureFolder(g_failed);
  }

bool EnsureFolder(const string path)
  {
   ResetLastError();
   if(FolderCreate(path))
      return true;

   int err = GetLastError();
   if(err == ERR_FILE_ALREADY_EXISTS)
      return true;

   PrintFormat("folder_error | path=%s code=%d", path, err);
   return false;
  }

void ProcessPending()
  {
   string mask = g_pending + "*.json";
   string file_name;
   int attributes;
   int handle = FileFindFirst(mask, file_name, attributes);
   if(handle == INVALID_HANDLE)
      return;

   do
     {
      bool completed = HandleIntentFile(file_name);
      FinalizeIntent(file_name, completed);
     }
   while(FileFindNext(handle, file_name, attributes));

   FileFindClose(handle);
  }

void FinalizeIntent(const string file_name, const bool succeeded)
  {
   const string target = succeeded ? g_done : g_failed;
   ResetLastError();
   if(FileMove(g_pending + file_name, target + file_name))
      return;

   int err = GetLastError();
   PrintFormat("intent_move_error | file=%s target=%s code=%d", file_name, target, err);
  }

bool HandleIntentFile(const string file_name)
  {
   const string rel_path = g_pending + file_name;
   const int handle = FileOpen(rel_path, FILE_READ | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
     {
      PrintFormat("intent_read_error | file=%s code=%d", file_name, GetLastError());
      return false;
     }

   string payload = "";
   while(!FileIsEnding(handle))
     {
      payload += FileReadString(handle);
     }
   FileClose(handle);

   CJAVal intent;
   if(!intent.Deserialize(payload))
     {
      PrintFormat("intent_parse_error | file=%s", file_name);
      return false;
     }

   string intent_id   = intent["intent_id"].ToStr();
   string policy_hash = intent["policy_hash"].ToStr();
   string mode        = intent["mode"].ToStr();

   if(mode != "LIVE")
     {
      PrintFormat("intent_ignored | intent_id=%s policy=%s mode=%s", intent_id, policy_hash, mode);
      return true;
     }

   string symbol = intent["symbol"].ToStr();
   string side   = intent["side"].ToStr();
   double quantity = intent["quantity"].ToDouble();
   double sl = ExtractOptionalDouble(intent["stop_loss"]);
   double tp = ExtractOptionalDouble(intent["take_profit"]);
   string tif = intent["time_in_force"].ToStr();

   if(quantity <= 0.0)
     {
      PrintFormat("intent_invalid_quantity | intent_id=%s qty=%.5f", intent_id, quantity);
      return false;
     }

   ResetLastError();
   bool success = false;
   ulong ticket = 0;

   if(StringCompare(side, "BUY") == 0)
      success = g_trade.Buy(quantity, symbol, 0.0, sl, tp, intent_id);
   else if(StringCompare(side, "SELL") == 0)
      success = g_trade.Sell(quantity, symbol, 0.0, sl, tp, intent_id);
   else
     {
      PrintFormat("intent_invalid_side | intent_id=%s side=%s", intent_id, side);
      return false;
     }

   if(success)
     {
      ticket = g_trade.ResultOrder();
      PrintFormat("intent_executed | intent_id=%s policy=%s ticket=%I64u qty=%.2f tif=%s", intent_id, policy_hash, ticket, quantity, tif);
      return true;
     }

   uint retcode = g_trade.ResultRetcode();
   string descr = g_trade.ResultRetcodeDescription();
   PrintFormat("intent_failed | intent_id=%s policy=%s retcode=%u message=%s", intent_id, policy_hash, retcode, descr);
   return false;
  }

double ExtractOptionalDouble(CJAVal &node)
  {
   if(node.Type() == JSON_TYPE_NUMBER)
      return node.ToDouble();
   return 0.0;
  }
