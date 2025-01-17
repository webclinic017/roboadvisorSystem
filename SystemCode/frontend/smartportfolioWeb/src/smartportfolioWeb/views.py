from django.views import generic
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.decorators import method_decorator
from django.shortcuts import redirect, render
from django.urls import reverse
# from django.shortcuts import get_object_or_404, redirect
# from profiles import models as p_models
# from profiles import forms as p_forms
# from django_tables2 import SingleTableView
# from profiles.models import Profile
# from profiles.tables import PortfolioTable
import numbers
import pickle
import pandas as pd
import os
from datetime import datetime
from collections import OrderedDict

import pytz
from bs4 import BeautifulSoup as bs
from .portfolio import calculate_portfolio, calculate_current_val

PORTFOLIO_SELECTION_PATH = "portfolio_details.xlsx"
PORTFOLIO_GRAPH_DATA_PATH = "portfolio_graph.pickle"
PORTFOLIO_PERF_DATA_PATH = "portfolio_details.pickle"

portfolio_selection = pd.read_excel(os.path.join(os.path.dirname(__file__), PORTFOLIO_SELECTION_PATH), index_col=0, dtype={'model': str, 'benchmark': str}, keep_default_na=False)
col_repl = {c: c.replace(" ", "_") for c in portfolio_selection.columns if " " in c}  # rename column if there is a space
portfolio_selection = portfolio_selection.rename(columns=col_repl)

with open(os.path.join(os.path.dirname(__file__), PORTFOLIO_GRAPH_DATA_PATH), "rb+") as f:
    portfolio_graph_data = pickle.load(f)

with open(os.path.join(os.path.dirname(__file__), PORTFOLIO_PERF_DATA_PATH), "rb+") as f:
    portfolio_perf_data = pickle.load(f)


class HomePage(generic.TemplateView):
    template_name = "home.html"


class AboutPage(generic.TemplateView):
    template_name = "about.html"


@login_required(login_url='/login')
def portfolio_reset(request, mode=None):
    # shortcut to reset all profile information for the logged in user
    p = request.user.profile
    p.avail_cash = 0
    p.asset_transfers = 0
    p.gross_asset_value = 0
    p.portfolio = {}

    if mode is None:
        messages.warning(request, "All portfolio values have been reset to zero")
    else:
        messages.warning(request, "Portfolio has been reset with some starting values")

        # Quick reset to some default starting values for everything
        p.avail_cash = 100000
        p.asset_transfers = 100000

        # quick method of buying
        saw_max_ret = "saw_all_weather_max_ret"
        crb = "crb_all_weather_crb"

        # simulate transactions in the past, including rebalancing by the system
        to_add = []
        to_add.append({'_id': saw_max_ret, 'amt': 15000, 'date': "2019-01-02 09:30", 'verbose': False, 'ttype': 'user_buy'})
        to_add.append({'_id': saw_max_ret, 'amt': -5000, 'date': "2019-02-18 09:30", 'verbose': False, 'ttype': 'user_sell'})
        to_add.append({'_id': saw_max_ret, 'amt': 8000, 'date': "2020-01-05 09:30", 'verbose': False, 'ttype': 'user_buy'})
        to_add.append({'_id': saw_max_ret, 'amt': -4000, 'date': "2020-02-10 09:30", 'verbose': False, 'ttype': 'user_sell'})
        for i in range(2, 13):
            to_add.append({'_id': saw_max_ret, 'amt': 0, 'date': f"2019-{i:02}-01 16:00", 'verbose': False, 'ttype': 'system'})

        to_add.append({'_id': crb, 'amt': 5000, 'date': "2019-06-02 09:30", 'verbose': False, 'ttype': 'user_buy'})
        for i in range(7, 13):
            to_add.append({'_id': crb, 'amt': 0, 'date': f"2019-{i:02}-01 16:01", 'verbose': False, 'ttype': 'system'})

        for i in range(1, 4):
            to_add.append({'_id': saw_max_ret, 'amt': 0, 'date': f"2020-{i:02}-01 16:00", 'verbose': False, 'ttype': 'system'})
            to_add.append({'_id': crb, 'amt': 0, 'date': f"2020-{i:02}-15 16:01", 'verbose': False, 'ttype': 'system'})

        # sort chronologically first, then execute in order
        to_add.sort(key=lambda a: a['date'])

        for a in to_add:
            portfolio_transact(request, **(a))

    p.save()

    # Redirect to portfolio page
    return redirect(reverse("portfolio_edit"))


def portfolio_sell(request, pid, amt, date=None, ttype='user_sell'):
    return portfolio_transact(request, pid, -amt, date=date, ttype=ttype)  # switch to negative amount


def portfolio_buy(request, pid, amt, date=None, ttype='user_buy'):
    return portfolio_transact(request, pid, amt, date=date, ttype=ttype)


@login_required(login_url='/login')
def portfolio_transact(request, _id, amt, timezone='US/Mountain', date=None, ttype='system', verbose=True):
    p = request.user.profile
    tz = pytz.timezone(timezone)
    transaction_date = datetime.now(tz) if date is None else tz.localize(datetime.strptime(date, '%Y-%m-%d %H:%M'))

    # find_name = portfolio_selection[portfolio_selection.index == "mpt_spdr_max_sharpe"]['name']
    portfolio_data = portfolio_selection[portfolio_selection.index == _id]

    if (portfolio_data.empty):
        messages.error(request, f"Portfolio with {_id} is not available")
    else:
        p_name = portfolio_data['name'][0]
        t = portfolio_data['type'][0]
        s = portfolio_data['stocks'][0]
        c = portfolio_data['criteria'][0]
        m = portfolio_data['model'][0]

        if (amt > p.avail_cash):
            messages.error(request, f"Investment of ${amt:,.2f} in {p_name.upper()} is not possible as you only have ${p.avail_cash:,.2f}!")

        else:
            # Create portfolio data if new, otherwise, we should retrieve existing transactions
            # p.portfolio = {}
            if p.portfolio is None: p.portfolio = {}
            if p.portfolio.get(_id, None) is None: p.portfolio[_id] = {"total_invested": 0, "transactions": []}

            # Add data, passing in existing transactions as this will be used for rebalancing
            stocks, invested, asset_value = calculate_portfolio(amt, p.portfolio[_id]["transactions"], t, s, c, m, transaction_date)

            diff = amt - invested

            if verbose:
                if invested > 0:
                    messages.success(request, f"Bought ${invested:,.2f} in {p_name.upper()} successfully! (${diff:,.2f} returned to avail cash)")
                else:
                    messages.success(request, f"Sold ${-invested:,.2f} in {p_name.upper()} successfully!")
            else:
                print(f'Transaction: ${invested:,.2f} in {p_name.upper()}')

            # Update all other existing pid with current value, to ensure a smooth plot
            # if ('user' in ttype):
            for pid in p.portfolio.keys():
                if (pid != _id):
                    p.portfolio[pid]['transactions'].append({
                        "type": "update",
                        "date": transaction_date,
                        "stocks": [],
                        "value_at_date": calculate_current_val(p.portfolio[pid]["transactions"], date=transaction_date)
                    })
                    # p.portfolio[pid]['transactions'].sort(key=lambda t: t["date"])

            # Add latest transaction
            transaction = {
                "type": ttype,
                "date": transaction_date,
                "stocks": stocks,
                "value_at_date": asset_value
            }

            # if (len(p.portfolio[_id]["transactions"]) > 0):
            #     last_entry = p.portfolio[_id]["transactions"][-1]
            #     if (last_entry["date"] == transaction_date and len(last_entry["stocks"]) == 0):
            #         p.portfolio[_id]["transactions"].pop()

            p.portfolio[_id]["transactions"].append(transaction)
            p.portfolio[_id]["total_invested"] += invested

            p.avail_cash -= invested  # substract amt used for investment
            p.save()

    # Redirect to portfolioEdit
    return redirect(reverse("portfolio_edit"))


@login_required(login_url='/login')
def portfolio_details(request, pid=""):
    extra_header_text = ""
    p_name = ""
    tb = ""
    date_range = []
    graph = []
    portfolio_data = portfolio_selection[portfolio_selection.index == pid]

    # Create drop-down list of possible portfolios to choose from
    all_names = dict(portfolio_selection['name'])
    all_names = OrderedDict(sorted(all_names.items(), key=lambda kv: kv[1], reverse=True))
    select = ""  # "<select id='select_portfolio'></select>"
    if pid == "":
        select += "<option disabled selected value> -- Select a Portfolio -- </option>"

    for i, name in all_names.items():
        if (pid != i):
            select += f"<option value='{i}'>{name}</option>"
        else:
            select += f"<option value='{i}' selected>{name}</option>"

    select = "<select id='select_portfolio'>" + select + "</select>"

    # Basically show the Tear Sheet and Graph for the chosen pid, and also comparison with benchmark
    if (not portfolio_data.empty):
        p_name = portfolio_data['name'][0]

        # Plot using highstocks
        index_list = portfolio_graph_data[pid][2][1].index.tolist()
        y = [v * 100 for v in portfolio_graph_data[pid][2][1]['algorithm_period_return'].values.tolist()]
        x = [int(t.timestamp() * 1000) for t in index_list]
        graph_data = [[x[i], y[i]] for i in range(len(x))]
        graph.append({
            "name": p_name,
            "data": graph_data,
            "visible": "true"
        })

        date_range = [index_list[0].strftime('%Y-%m-%d'), index_list[-1].strftime('%Y-%m-%d')]

        if (portfolio_data['benchmark'][0] != ""):
            p_benchmarks = [f"bah_{x.strip()}_bah" for x in portfolio_data['benchmark'][0].split(',')]

            # Concatenate perf matrices for PID and its benchmarks
            df_with_bm = pd.concat([portfolio_perf_data[pid]] + [portfolio_perf_data[b] for b in p_benchmarks], axis=1)

            # Show table of performance statistics, modifying for formatting
            # soup = bs(portfolio_perf_data[pid].to_html(), 'html.parser')
            soup = bs(df_with_bm.to_html(), 'html.parser')
            tb = soup.table

            # Add tooltip
            tooltipMap = {
                '1mth': 'last 1 month data',
                '1year': 'last 1 year data',
                'All': f'all available data from {date_range[0]} to {date_range[1]}'
            }
            for t in tb.thead.find_all('th'):
                if t.text in tooltipMap.keys():
                    t['title'] = f'Backtesting on {tooltipMap.get(t.text)}'
                    t.append(bs("<i class='fa fa-question-circle' style='float:right;'></i>", 'html.parser'))

            # construct additional header
            # extra_header = f"<tr><th></th>{'<th colspan=\'3\'></th>' * (len(p_benchmarks)+1)}</tr>"
            extra_row = soup.new_tag("tr")
            extra_header = '<th></th><th class=\'highlight\' colspan=\'3\'>' + p_name + '</th>'

            for b in p_benchmarks:
                th_name = portfolio_selection[portfolio_selection.index == b]['name'][0]
                extra_header += '<th colspan=\'3\'>' + th_name + '</th>'

                # also add data to plots
                y = [v * 100 for v in portfolio_graph_data[b][2][1]['algorithm_period_return'].values.tolist()]
                x = [int(t.timestamp() * 1000) for t in portfolio_graph_data[b][2][1].index.tolist()]
                graph_data = [[x[i], y[i]] for i in range(len(x))]
                graph.append({
                    "name": th_name,
                    "data": graph_data
                })

            extra_row.append(bs(extra_header, 'html.parser'))
            tb.thead.insert(0, extra_row)

            extra_header_text = "<h3>in comparison with relevant benchmarks</h3>"

        else:
            soup = bs(portfolio_perf_data[pid].to_html(), 'html.parser')
            tb = soup.table

        # Add css styling for certain cells based on positive or negative
        for t in tb.find_all('tr'):
            if t.th.string in ["Annual return", "Cumulative returns", "Sharpe ratio"]:
                for d in t.find_all('td'):
                    if d.string is not None:
                        v = float(d.string.replace("%", ""))
                        d['class'] = "pos" if v > 0 else "neg"

        tb['class'] = "table portfolio"
        tb = str(tb)

    return render(request, 'portfolio_details.html', {'name': p_name, 'range': date_range,
        'selection': select, 'table': tb, 'extra_header': extra_header_text, 'graph': graph})


@method_decorator(login_required(login_url='/login'), name='dispatch')
class PortfolioEditPage(generic.TemplateView):
    template_name = "portfolio_edit.html"
    # table_class = PortfolioTable

    def dispatch(self, request, *args, **kwargs):
        p = self.request.user.profile

        # portfolios will be displayed using jquery datatables in template
        kwargs["avail_portfolios"] = portfolio_selection.T.to_dict()

        # get current portfolios
        if p.portfolio is None: p.portfolio = {}
        current_portfolios = dict.fromkeys(p.portfolio.keys())
        all_portfolios = portfolio_selection.to_dict("index")

        gross_asset_value = 0
        for k in current_portfolios:
            current_portfolios[k] = all_portfolios[k]
            current_portfolios[k]["total_invested"] = p.portfolio[k]["total_invested"]
            current_portfolios[k]["current_value"] = calculate_current_val(p.portfolio[k]["transactions"])
            current_portfolios[k]["earnings"] = current_portfolios[k]["current_value"] - current_portfolios[k]["total_invested"]

            gross_asset_value += current_portfolios[k]["current_value"]

            a_var = max(5, -current_portfolios[k]['annual_99%-var'])  # lower bound of 5% (just in case a_var is positive)
            r_title = f"The portfolio has a 99% probability of not losing more than {a_var:.2f}% in a year."
            current_portfolios[k]["risk_title"] = r_title

            current_portfolios[k]["class"] = {}
            for attr, v in current_portfolios[k].items():
                if isinstance(v, numbers.Number):
                    current_portfolios[k]["class"][attr] = "pos" if v > 0 else "neg"

        p.gross_asset_value = gross_asset_value  # update gross asset value
        p.save()

        # data for summary table
        kwargs["account"] = p.gross_asset_value + p.avail_cash
        kwargs["earnings"] = kwargs["account"] - p.asset_transfers
        kwargs["cash"] = p.avail_cash

        kwargs["account_title"] = "Sum of gross asset value and available cash"
        kwargs["asset_title"] = "Total transfers into account"
        kwargs["earnings_title"] = "Difference between account and asset transfers"
        kwargs["gross_asset_title"] = "How much your assets are worth now"
        kwargs["cash_title"] = "Available cash that can be used for investment"

        kwargs["current_portfolios"] = current_portfolios

        # #16 - Also plot the transaction history/ value at each rebalance
        user_port = request.user.profile.portfolio
        trans_graph = []
        trans_annotate_B = []
        trans_annotate_S = []

        for pid in user_port.keys():
            # pid = "saw_all_weather_max_ret"
            user_port_pid = user_port.get(pid, None)
            if (user_port_pid is not None):
                trans = user_port_pid['transactions']
                trans_graph_data = []
                for t in trans:
                    x = int(t['date'].timestamp() * 1000)
                    y = float(t['value_at_date'])

                    # To simplify things, since this is a stacked chart, we will place annotation on x-axis
                    # In order to style them differently, we will place them in separate arrays
                    tt = t['type']
                    if (tt == "user_buy"):
                        trans_annotate_B.append({
                            'point': {'xAxis': 0, 'yAxis': 0, 'x': x, 'y': 0}, 'text': 'B'
                        })
                    elif (tt == "user_sell"):
                        trans_annotate_S.append({
                            'point': {'xAxis': 0, 'yAxis': 0, 'x': x, 'y': 0}, 'text': 'S'
                        })
                    # if tt in ['user_buy', 'user_sell']:
                    #     text = 'B' if tt == 'user_buy' else 'S'
                    #     trans_annotations.append({
                    #         'point': {'xAxis': 0, 'yAxis': 0, 'x': x, 'y': y}, 'y': 0, 'shape': 'circle', 'text': text
                    #     })

                    trans_graph_data.append([x, y])

                trans_graph.append({
                    "name": all_portfolios[pid]['name'],
                    "data": trans_graph_data,
                    "visible": "true"
                })

        kwargs["trans_graph"] = trans_graph
        kwargs["trans_annotate_B"] = trans_annotate_B
        kwargs["trans_annotate_S"] = trans_annotate_S

        return super(PortfolioEditPage, self).dispatch(request, *args, **kwargs)


@method_decorator(login_required(login_url='/login'), name='dispatch')
class PortfolioPage(generic.TemplateView):
    template_name = "portfolio.html"

    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        asset = user.profile

        kwargs["account"] = asset.gross_asset_value + asset.avail_cash
        kwargs["earnings"] = kwargs["account"] - asset.asset_transfers

        kwargs["account_title"] = "Sum of gross asset value and available cash"
        kwargs["asset_title"] = "Total transfers into account"
        kwargs["earnings_title"] = "Difference between account and asset transfers"
        kwargs["gross_asset_title"] = "How much your assets are worth now"
        kwargs["cash_title"] = "Available cash that can be used for investment"

        return super(PortfolioPage, self).dispatch(request, *args, **kwargs)
