console.log("create_account.js LOADED");

document.addEventListener("DOMContentLoaded", () => {

    /* -------------------------------
       ELEMENT REFERENCES
    -------------------------------- */
    const roleDriver = document.getElementById("role_driver");
    const roleEnthusiast = document.getElementById("role_enthusiast");

    const companySection = document.getElementById("company_section");
    const regionSection = document.getElementById("region_section");
    const depotSection = document.getElementById("depot_section");
    const outstationSection = document.getElementById("outstation_section");

    const companySelect = document.getElementById("company_select");
    const regionSelect = document.getElementById("region_select");
    const depotSelect = document.getElementById("depot_select");
    const outstationSelect = document.getElementById("outstation_select");

    const finalCompany = document.getElementById("final_company");
    const finalRegion = document.getElementById("final_region");
    const finalDepot = document.getElementById("final_depot");
    const finalOutstation = document.getElementById("final_outstation");

    const finalLevel = document.getElementById("final_level");
    const final_role_driver = document.getElementById("final_role_driver");
    const final_role_enthusiast = document.getElementById("final_role_enthusiast");


    /* -------------------------------
       ROLE VISIBILITY + LEVEL LOGIC
    -------------------------------- */
    function updateRoleVisibility() {

        // Independent role flags
        final_role_driver.value = roleDriver.checked ? 1 : 0;
        final_role_enthusiast.value = roleEnthusiast.checked ? 1 : 0;

        // LEVEL LOGIC
        // Driver = 3
        // Enthusiast = 4
        // Driver + Enthusiast = 3
        finalLevel.value = roleDriver.checked ? "3" : "4";

        // VISIBILITY LOGIC
        if (roleDriver.checked) {

            companySection.style.display = "block";
            regionSection.style.display = "block";
            depotSection.style.display = "block";
            outstationSection.style.display = "block";

        } else {

            companySection.style.display = "none";
            regionSection.style.display = "none";
            depotSection.style.display = "none";
            outstationSection.style.display = "none";

            finalCompany.value = "None";
            finalRegion.value = "None";
            finalDepot.value = "None";
            finalOutstation.value = "None";
        }
    }

    roleDriver.addEventListener("change", updateRoleVisibility);
    roleEnthusiast.addEventListener("change", updateRoleVisibility);


    /* -------------------------------
       COMPANY SELECTION
    -------------------------------- */
    companySelect.addEventListener("change", () => {
        const company = companySelect.value;
        finalCompany.value = company;

        const companyData = companyRegions[company];

        // Reset region dropdown
        regionSelect.innerHTML = `
            <option value="">-- Select Region --</option>
            <option value="Other">Other (add your Region)</option>
        `;

        depotSection.style.display = "none";
        outstationSection.style.display = "none";

        if (!companyData) {
            regionSection.style.display = "none";
            return;
        }

        // REGIONAL COMPANY
        if (companyData.regions) {
            Object.keys(companyData.regions).forEach(region => {
                const opt = document.createElement("option");
                opt.value = region;
                opt.textContent = region;
                regionSelect.appendChild(opt);
            });

            regionSection.style.display = "block";
            return;
        }

        // MAIN COMPANY
        if (companyData.main) {
            regionSection.style.display = "none";

            loadDepots(companyData.main.depots);
            loadOutstations(companyData.main.outstations);
        }
    });


    /* -------------------------------
       REGION SELECTION
    -------------------------------- */
    regionSelect.addEventListener("change", () => {
        const company = companySelect.value;
        const region = regionSelect.value.trim();

        finalRegion.value = region;

        if (region === "" || region === "Other") {
            depotSection.style.display = "none";
            outstationSection.style.display = "none";
            return;
        }

        const companyData = companyRegions[company];

        if (!companyData || !companyData.regions) {
            depotSection.style.display = "none";
            outstationSection.style.display = "none";
            return;
        }

        const regionData = companyData.regions[region];

        if (!regionData) {
            console.warn("Region not found:", region);
            depotSection.style.display = "none";
            outstationSection.style.display = "none";
            return;
        }

        loadDepots(regionData.depots);
        loadOutstations(regionData.outstations);
    });


    /* -------------------------------
       DEPOT + OUTSTATION SELECTION
    -------------------------------- */
    depotSelect.addEventListener("change", () => {
        finalDepot.value = depotSelect.value;
    });

    outstationSelect.addEventListener("change", () => {
        finalOutstation.value = outstationSelect.value;
    });


    /* -------------------------------
       INITIAL LOAD
    -------------------------------- */
    loadCompanies();      // MUST run once
    updateRoleVisibility(); // Apply visibility rules

});
