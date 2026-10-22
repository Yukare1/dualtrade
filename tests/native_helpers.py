"""Run the production MQL zone/selection bodies in a small C# compatibility host.

Not a broker/tick integration test. Tests closed-bar boundaries, invalidation,
independent touches, strategy conflict, and target obstacles without a login.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"


def functions(path):
    source = path.read_text(encoding="utf-8-sig")
    output = {}
    for match in re.finditer(r"\b(bool|int|double|void)\s+(\w+)\s*\(([^{}]*?)\)\s*\{", source):
        opening = source.index("{", match.start())
        depth, end = 1, opening + 1
        while depth:
            depth += (source[end] == "{") - (source[end] == "}")
            end += 1
        output[match[2]] = (match[1], match[3], source[opening:end])
    return output


def adapt_body(body, references):
    for name, indices in references.items():
        cursor = 0
        while match := re.search(r"\b" + name + r"\s*\(", body[cursor:]):
            opening = cursor + match.end() - 1
            depth, end, start = 1, opening + 1, opening + 1
            args = []
            while depth:
                char = body[end]
                if char == "(": depth += 1
                elif char == ")": depth -= 1
                elif char == "," and depth == 1:
                    args.append(body[start:end]); start = end + 1
                end += 1
            args.append(body[start:end - 1])
            for index in indices:
                args[index] = "ref " + args[index].strip()
            replacement = ",".join(args)
            body = body[:opening + 1] + replacement + body[end - 1:]
            cursor = opening + len(replacement) + 2
    body = re.sub(r"\b(bool|int|FXZone|FXCandidate|MqlRates)\s+(\w+)\[\];",
                  r"\1[] \2=new \1[0];", body)
    body = re.sub(r"MqlRates (\w+)=\{\};", r"MqlRates \1=new MqlRates();", body)
    body = re.sub(r"(double|string|int) (\w+)\[\]=\{([^}]+)\};", r"\1[] \2=new \1[]{\3};", body)
    return body


SHIM = r'''
using System;
class Engine {
struct FXFibSwing { public int direction; public double origin,end; public long origin_time,end_time; }
struct MqlRates { public double open,high,low,close; public long time; }
struct FXZone { public double low,high,center,score; public int touches,source; public bool support; public long newest; }
struct FXCandidate { public int direction; public string setup; public double anchor,stop_anchor,score,objective; public bool structure; }
struct FXStrategySettings { public bool rejection,breakout,pullback,range,retest,strict_retest,strict_rejection; public int breakout_bars,retest_max_bars; public double level_tolerance,retest_tolerance,range_adx,max_retest_atr,max_retest_rsi,min_rejection_score; }
struct FXStrategyContext { public int support_index,resistance_index,regime,retest_dir; public bool up,down; public double atr,adx,rsi,prior_rsi,entry_ma,prior_ma,middle_band,upper_band,lower_band,retest_level; public long now,retest_bar; }
static int ArraySize<T>(T[] a) { return a.Length; }
static void ArrayResize<T>(ref T[] a,int n) { Array.Resize(ref a,n); }
static void ArrayInitialize<T>(T[] a,T value) { for(int i=0;i<a.Length;i++) a[i]=value; }
static double MathAbs(double v) { return Math.Abs(v); }
static double MathMax(double a,double b) { return Math.Max(a,b); }
static int MathMax(int a,int b) { return Math.Max(a,b); }
static double MathMin(double a,double b) { return Math.Min(a,b); }
static int MathMin(int a,int b) { return Math.Min(a,b); }
static bool MathIsValidNumber(double v) { return !Double.IsNaN(v) && !Double.IsInfinity(v); }
static void Print(string v) { Console.WriteLine(v); }
static void Check(string name,bool v) { if(!v) throw new Exception(name); Console.WriteLine("PASS "+name); }
'''

