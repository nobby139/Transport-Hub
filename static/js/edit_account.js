console.log("SCRIPT START");
console.log("EDIT PAGE JS LOADED");

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

    const dateOfBirth = document.getElementById("date_of_birth");

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
       AGE CALCULATION
    -------------------------------- */
    function calculateAge(dob) {
        const diff = Date.now() - dob.getTime();
        const ageDate = new Date(diff);
        return Math.abs(ageDate.getUTCFullYear() - 1970);
    }

    /* -------------------------------
       ROLE VISIBILITY + LEVEL LOGIC
    -------------------------------- */
    function updateRoleVisibility() {
        final_role_driver.value = roleDriver.checked ? 1 : 0;
        final_role_enthusiast.value = roleEnthusiast.checked ? 1 : 0;
        finalLevel.value = roleDriver.checked ? "3" : "4";

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
    updateRoleVisibility();

    /* -------------------------------
       COMPANY SELECTION
    -------------------------------- */
    companySelect.addEventListener("change", () => {
        const company = companySelect.value;
        finalCompany.value = company;

        const companyData = companyRegions[company];

        regionSelect.innerHTML = `
            <option value="">-- Select Region --</option>
            <option value="Other">Other (add your Region)</option>
        `;

        if (!companyData) {
            regionSection.style.display = "none";
            depotSection.style.display = "none";
            outstationSection.style.display = "none";
            return;
        }

        if (companyData.regions) {
            Object.keys(companyData.regions).forEach(region => {
                const opt = document.createElement("option");
                opt.value = region;
                opt.textContent = region;
                regionSelect.appendChild(opt);
            });
            regionSection.style.display = "block";
            depotSection.style.display = "none";
            outstationSection.style.display = "none";
        } else if (companyData.main) {
            finalRegion.value = "None";
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
        const region = regionSelect.value;
        finalRegion.value = region;

        if (region === "" || region === "Other") {
            depotSection.style.display = "none";
            outstationSection.style.display = "none";
            return;
        }

        const companyData = companyRegions[company];
        if (!companyData || !companyData.regions || !companyData.regions[region]) {
            console.warn("Region not found for company:", company, region);
            depotSection.style.display = "none";
            outstationSection.style.display = "none";
            return;
        }

        const regionData = companyData.regions[region];
        loadDepots(regionData.depots);
        loadOutstations(regionData.outstations);
    });

    /* -------------------------------
       LOAD DEPOTS
    -------------------------------- */
    function loadDepots(depots) {
        depotSelect.innerHTML = `
            <option value="">-- Select Depot --</option>
            <option value="Other">Other (add your Depot)</option>
        `;
        depots.forEach(depot => {
            const opt = document.createElement("option");
            opt.value = depot;
            opt.textContent = depot;
            depotSelect.appendChild(opt);
        });
        depotSection.style.display = "block";
        depotSelect.addEventListener("change", () => {
            finalDepot.value = depotSelect.value;
        });
    }

    /* -------------------------------
       LOAD OUTSTATIONS
    -------------------------------- */
    function loadOutstations(outstations) {
        outstationSelect.innerHTML = `
            <option value="None">None</option>
            <option value="Other">Other (add your Outstation)</option>
        `;
        if (outstations && outstations.length > 0) {
            outstations.forEach(out => {
                const opt = document.createElement("option");
                opt.value = out;
                opt.textContent = out;
                outstationSelect.appendChild(opt);
            });
        }
        outstationSection.style.display = "block";
        outstationSelect.addEventListener("change", () => {
            finalOutstation.value = outstationSelect.value;
        });
    }

    /* -------------------------------
       FORM SUBMISSION
    -------------------------------- */
    document.getElementById("editForm").addEventListener("submit", async (e) => {
        e.preventDefault();

        const error = document.getElementById("error");
        const success = document.getElementById("success");

        error.textContent = "";
        success.textContent = "";

        const dob = dateOfBirth.value;
        const birthDate = new Date(dob);
        const today = new Date();
        let age = today.getFullYear() - birthDate.getFullYear();
        const m = today.getMonth() - birthDate.getMonth();
        if (m < 0 || (m === 0 && today.getDate() < birthDate.getDate())) age--;

        if (roleDriver.checked && age < 18) {
            error.textContent = "You must be 18 or older to have driver access.";
            return;
        }
        if (roleEnthusiast.checked && age < 16) {
            error.textContent = "You must be 16 or older to have enthusiast access.";
            return;
        }

        const payload = {
            first_name: document.getElementById("first_name").value.trim(),
            last_name: document.getElementById("last_name").value.trim(),
            username: document.getElementById("username").value.trim(),
            email: document.getElementById("email").value.trim(),
            date_of_birth: dob,
            role_driver: roleDriver.checked ? 1 : 0,
            role_enthusiast: roleEnthusiast.checked ? 1 : 0,
            company: finalCompany.value,
            region: finalRegion.value,
            depot: finalDepot.value,
            outstation: finalOutstation.value,
            level: finalLevel.value
        };

        const res = await fetch("/edit_profile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const data = await res.json();

        if (!res.ok) {
            error.textContent = data.message || "Update failed.";
            return;
        }

        success.textContent = "Profile updated successfully!";
        setTimeout(() => window.location.href = "/profile", 1500);
    });

    console.log("SCRIPT END");
});
