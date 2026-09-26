console.log("companyRegions.js LOADED");

/* -------------------------------
   COMPANY + REGION DATA
-------------------------------- */
const companyRegions = {
    "None": { "regions": {}, "main": null },

    "Abellio London": {
        "main": {
            "depots": ["Battersea Garage", "Hayes Garage", "Southall Garage", "Twickenham Garage", "Walworth Garage"],
            "outstations": []
        }
    },

    "Arriva": {
        "regions": {
            "Midlands": {
                "depots": ["Cannock", "Shrewsbury", "Telford"],
                "outstations": ["Rugeley", "Stafford"]
            },
            "North East": {
                "depots": ["Darlington", "Durham", "Redcar"],
                "outstations": ["Bishop Auckland", "Peterlee"]
            },
            "North West": {
                "depots": ["Bootle", "Liverpool", "Speke", "Wythenshawe"],
                "outstations": ["Birkenhead", "Southport"]
            },
            "South": {
                "depots": ["Aylesbury", "Hemel Hempstead", "High Wycombe"],
                "outstations": ["Luton", "Milton Keynes"]
            }
        }
    },

    "Barnes Coaches": {
        "main": {
            "depots": ["Swindon Depot"],
            "outstations": []
        }
    },

    "Beeline Coaches": {
        "main": {
            "depots": ["Wiltshire Depot"],
            "outstations": []
        }
    },

    "Berrys Coaches": {
        "main": {
            "depots": ["Taunton Depot"],
            "outstations": ["Bridgwater", "Wellington"]
        }
    },

    "Bluestar": {
        "main": {
            "depots": ["Southampton Depot"],
            "outstations": ["Eastleigh", "Hedge End", "Totton"]
        }
    },

    "Chandlers": {
        "main": {
            "depots": ["Westbury Depot"],
            "outstations": []
        }
    },

    "Coachstyle": {
        "main": {
            "depots": ["Salisbury Depot"],
            "outstations": []
        }
    },

    "Damory": {
        "main": {
            "depots": ["Blandford Depot"],
            "outstations": ["Gillingham", "Shaftesbury"]
        }
    },

    "Diamond Bus": {
        "regions": {
            "North West": {
                "depots": ["Preston"],
                "outstations": ["Blackburn"]
            },
            "West Midlands": {
                "depots": ["Kidderminster", "Tividale"],
                "outstations": ["Bromsgrove", "Redditch"]
            }
        }
    },

    "Edwards Coaches": {
        "main": {
            "depots": ["Pontypridd Depot"],
            "outstations": ["Cardiff", "Newport"]
        }
    },

    "Faresaver": {
        "main": {
            "depots": ["Chippenham Depot"],
            "outstations": [
                "Frome Outstation",
                "Melksham Outstation",
                "West Ashton Outstation",
                "Worton Outstation"
            ]
        }
    },

    "First Bus": {
        "regions": {
            "Midlands": {
                "depots": ["Derby", "Leicester", "Worcester"],
                "outstations": ["Hereford", "Stoke-on-Trent"]
            },
            "Scotland": {
                "depots": ["Aberdeen", "Glasgow"],
                "outstations": ["Falkirk", "Livingston", "Stirling"]
            },
            "South Wales": {
                "depots": ["Cardiff", "Newport", "Swansea"],
                "outstations": ["Aberdare", "Cwmbran", "Pontypridd", "Rhondda"]
            },
            "South West": {
                "depots": ["Bath", "Exeter", "Hengrove", "Lawrence Hill", "Plymouth", "Taunton", "Weston-super-Mare"],
                "outstations": ["Bridgwater", "Newquay", "Truro", "Wells", "Yeovil"]
            },
            "Yorkshire": {
                "depots": ["Bradford", "Halifax", "Leeds"],
                "outstations": ["Huddersfield"]
            }
        }
    },

    "Go-Ahead Group": {
        "regions": {
            "London": {
                "depots": ["Bexleyheath", "Merton", "New Cross", "Putney"],
                "outstations": []
            },
            "North East": {
                "depots": ["Gateshead", "Sunderland"],
                "outstations": ["Consett", "Peterlee"]
            },
            "South": {
                "depots": ["Brighton", "Worthing"],
                "outstations": ["Eastbourne", "Lewes"]
            }
        }
    },

    "Lothian Buses": {
        "main": {
            "depots": ["Annandale Street Depot"],
            "outstations": ["Longstone Depot", "Marine Depot"]
        }
    },

    "Metroline": {
        "main": {
            "depots": ["Edgware", "Holloway", "Perivale", "Potters Bar", "Willesden"],
            "outstations": []
        }
    },

    "Morebus": {
        "main": {
            "depots": ["Poole Depot"],
            "outstations": ["Bournemouth", "Swanage", "Wimborne"]
        }
    },

    "National Express Bus": {
        "regions": {
            "Coventry": {
                "depots": ["Coventry Depot"],
                "outstations": ["Bedworth", "Nuneaton"]
            },
            "West Midlands": {
                "depots": ["Birmingham Central", "Walsall", "West Bromwich", "Yardley Wood"],
                "outstations": ["Acocks Green", "Sutton Coldfield"]
            }
        }
    },

    "Reading Buses": {
        "main": {
            "depots": ["Reading Depot"],
            "outstations": ["Newbury", "Wokingham"]
        }
    },

    "Salisbury Reds": {
        "main": {
            "depots": ["Salisbury Depot"],
            "outstations": ["Amesbury", "Andover (limited)", "Devizes", "Wilton"]
        }
    },

    "South West Coaches": {
        "main": {
            "depots": ["Wincanton Depot"],
            "outstations": ["Gillingham", "Shaftesbury", "Yeovil"]
        }
    },

    "Southern Vectis": {
        "main": {
            "depots": ["Newport Depot"],
            "outstations": ["Cowes", "Ryde", "Shanklin"]
        }
    },

    "Stagecoach": {
        "regions": {
            "Midlands": {
                "depots": ["Leamington", "Nuneaton", "Stratford"],
                "outstations": ["Coventry", "Rugby"]
            },
            "North East": {
                "depots": ["Newcastle", "Sunderland"],
                "outstations": ["Gateshead", "South Shields"]
            },
            "Scotland": {
                "depots": ["Aberdeen", "Dundee", "Perth"],
                "outstations": ["Elgin", "Inverness"]
            },
            "South": {
                "depots": ["Andover", "Basingstoke", "Winchester"],
                "outstations": ["Alton", "Salisbury"]
            },
            "South West": {
                "depots": ["Barnstaple", "Exeter", "Torquay"],
                "outstations": ["Newton Abbot", "Okehampton"]
            }
        }
    },

    "Transdev": {
        "regions": {
            "Yorkshire": {
                "depots": ["Harrogate", "Keighley"],
                "outstations": ["Ripon", "Skipton"]
            }
        }
    }
};


/* -------------------------------
   LOAD COMPANIES
-------------------------------- */
function loadCompanies() {
    const companySelect = document.getElementById("company_select");

    companySelect.innerHTML = `
        <option value="">-- Select Company --</option>
        <option value="Other">Other (add your Company)</option>
    `;

    Object.keys(companyRegions).forEach(company => {
        if (company !== "None") {
            const opt = document.createElement("option");
            opt.value = company;
            opt.textContent = company;
            companySelect.appendChild(opt);
        }
    });
}


/* -------------------------------
   LOAD DEPOTS
-------------------------------- */
function loadDepots(depots) {
    const depotSelect = document.getElementById("depot_select");

    depotSelect.innerHTML = `
        <option value="">-- Select Depot --</option>
        <option value="Other">Other (add your Depot)</option>
    `;

    if (!Array.isArray(depots)) return;

    depots.forEach(depot => {
        const opt = document.createElement("option");
        opt.value = depot;
        opt.textContent = depot;
        depotSelect.appendChild(opt);
    });

    document.getElementById("depot_section").style.display = "block";
}


/* -------------------------------
   LOAD OUTSTATIONS
-------------------------------- */
function loadOutstations(outstations) {
    const outstationSelect = document.getElementById("outstation_select");

    outstationSelect.innerHTML = `
        <option value="None">None</option>
        <option value="Other">Other (add your Outstation)</option>
    `;

    if (Array.isArray(outstations)) {
        outstations.forEach(out => {
            const opt = document.createElement("option");
            opt.value = out;
            opt.textContent = out;
            outstationSelect.appendChild(opt);
        });
    }

    document.getElementById("outstation_section").style.display = "block";
}
