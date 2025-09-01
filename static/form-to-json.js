function formToJson(elementName) {
    const container = document.getElementById(elementName);
    if (!container) {
        console.error('Element with id "' + elementName + '" not found');
        return {};
    }
    
    const formElements = container.querySelectorAll('input, select, textarea');
    const result = {};
    
    function setNestedValue(obj, keys, value) {
        if (keys.length === 1) {
            obj[keys[0]] = value;
            return;
        }
        
        const key = keys[0];
        if (!obj[key]) obj[key] = {};
        const nextObj = obj[key];
        
        setNestedValue(nextObj, keys.slice(1), value);
    }
    
    formElements.forEach(function(element) {
        const id = element.id;
        if (!id) return;
        
        let value = element.value;
        
        if (element.type === 'number') {
            value = value === '' ? null : Number(value);
        } else if (element.type === 'checkbox') {
            value = element.checked;
        } else if (element.type === 'radio' && !element.checked) {
            return;
        }
        
        const keys = id.split('.');
        setNestedValue(result, keys, value);
    });
    
    return result;
}

function jsonToForm(data, prefix = '') {
    for (const [key, value] of Object.entries(data)) {
        const elementId = prefix ? `${prefix}.${key}` : key;

        // Handle nested objects
        if (value && typeof value === 'object' && !Array.isArray(value)) {
            jsonToForm(value, elementId);
            continue;
        }

        const element = document.getElementById(elementId);
        if (element) {
            if (element.tagName === 'INPUT') {
                if (element.type === 'checkbox') {
                    element.checked = Boolean(value);
                } else if (element.type === 'radio') {
                    element.checked = (element.value === value);
                } else {
                    element.value = value;
                }
            } else {
                element.textContent = value;
            }
        }
    }
}

/* Example usage:

// Get data from form
// const data = formToJson('order');
// console.log(data);
//
// Update form with data
// data = { order: { ... } }
// jsonToForm(data);

// Load on Document Ready

// A very short form, put it just before </body>:
fetch('config.json').then(r=>r.json()).then(d=>jsonToForm(d));

// Or a longer version:
document.addEventListener('DOMContentLoaded', function() {
    fetch('/api/data')
        .then(response => response.json())
        .then(data => jsonToForm(data))
        .catch(error => {
            console.error('Error fetching data:', error);
        });
});

// Load at regular intervals
setInterval(function() {
    fetch('/api/data')
        .then(response => response.json())
        .then(data => jsonToForm(data))
        .catch(error => {
            console.error('Error fetching data:', error);
        });
}, 5000);

// post data
function postData() {
    const data = formToJson('order');
    fetch('/api/data', {
        method: 'POST',
        body: JSON.stringify(data)
    });
}

// Update with Event Source
let eventSource = new EventSource('/api/events');
eventSource.onmessage = function(event) {
    jsonToForm(JSON.parse(event.data));
};
eventSource.onerror = function(error) {
    console.error('Error fetching data:', error);
};

// Stop EventSource
eventSource.close();
eventSource = null;

*/
